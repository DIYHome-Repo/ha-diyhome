"""Switch platform — valvola 1 e valvola 2."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator
from .const import DOMAIN
from .entity import DiyHomeEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DiyHomeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]

    entities = []
    for uid, device in coordinator.data.items():
        if device.get("valve1") is not None:
            entities.append(DiyHomeValveSwitch(coordinator, client, uid, valve=1))
        if device.get("valve2") is not None:
            entities.append(DiyHomeValveSwitch(coordinator, client, uid, valve=2))

    async_add_entities(entities)


class DiyHomeValveSwitch(DiyHomeEntity, SwitchEntity):
    """Represents a DiyHome valve as a HA switch."""

    def __init__(self, coordinator, client, uid: str, valve: int) -> None:
        super().__init__(coordinator, uid)
        self._client = client
        self._valve = valve
        self._attr_unique_id = f"{uid}_valve{valve}"
        self._attr_icon = "mdi:valve"

    @property
    def name(self) -> str:
        data = self._device_data
        valve_data = data.get(f"valve{self._valve}", {})
        return valve_data.get("name") or f"Valvola {self._valve}"

    @property
    def is_on(self) -> bool | None:
        data = self._device_data
        valve_data = data.get(f"valve{self._valve}")
        if valve_data is None:
            return None
        return valve_data.get("is_open", False)

    @property
    def available(self) -> bool:
        return self._device_data.get("online", False)

    async def async_turn_on(self, **kwargs) -> None:
        action = f"valve{self._valve}_open" if self._valve == 2 else "valve_open"
        await self._client.send_command(self._uid, action)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        action = f"valve{self._valve}_close" if self._valve == 2 else "valve_close"
        await self._client.send_command(self._uid, action)
        await self.coordinator.async_request_refresh()
