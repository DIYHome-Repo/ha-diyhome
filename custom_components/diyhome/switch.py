"""Switch platform — valvola principale, valvola 2, zone irrigazione."""
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

    _LOGGER.warning(
        "DiyHome switch setup: coordinator.data has %d device(s): %s",
        len(coordinator.data),
        list(coordinator.data.keys()),
    )
    entities: list[SwitchEntity] = []
    for uid, device in coordinator.data.items():
        entities.append(DiyHomeValveSwitch(coordinator, client, uid, valve=1))
        if device.get("valve2") is not None:
            entities.append(DiyHomeValveSwitch(coordinator, client, uid, valve=2))
        for zone in device.get("zones", []):
            entities.append(DiyHomeZoneSwitch(coordinator, client, uid, zone["index"], zone["name"]))

    async_add_entities(entities)


class DiyHomeValveSwitch(DiyHomeEntity, SwitchEntity):
    """Rappresenta una valvola DiyHome come switch HA."""

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
        valve_data = self._device_data.get(f"valve{self._valve}")
        if valve_data is None:
            return None
        return valve_data.get("is_open", False)

    @property
    def available(self) -> bool:
        # FIX: include super().available → unavailable quando il coordinator fallisce
        return super().available and self._device_data.get("online", False)

    async def async_turn_on(self, **kwargs) -> None:
        action = f"valve{self._valve}_open" if self._valve == 2 else "valve_open"
        await self._client.send_command(self._uid, action)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        action = f"valve{self._valve}_close" if self._valve == 2 else "valve_close"
        await self._client.send_command(self._uid, action)
        await self.coordinator.async_request_refresh()


class DiyHomeZoneSwitch(DiyHomeEntity, SwitchEntity):
    """Rappresenta una zona irrigazione DiyHome come switch HA."""

    def __init__(self, coordinator, client, uid: str, zone_index: int, zone_name: str) -> None:
        super().__init__(coordinator, uid)
        self._client = client
        self._zone_index = zone_index
        self._zone_name = zone_name
        self._attr_unique_id = f"{uid}_zone_{zone_index}"
        self._attr_icon = "mdi:sprinkler-variant"

    @property
    def name(self) -> str:
        zone = self._get_zone()
        return zone.get("name") or self._zone_name or f"Zona {self._zone_index + 1}"

    def _get_zone(self) -> dict:
        for z in self._device_data.get("zones", []):
            if z.get("index") == self._zone_index:
                return z
        return {}

    @property
    def is_on(self) -> bool:
        return self._get_zone().get("is_active", False)

    @property
    def available(self) -> bool:
        # FIX: include super().available → unavailable quando il coordinator fallisce
        return super().available and self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        zone = self._get_zone()
        attrs: dict = {"zone_index": self._zone_index, "zone_type": zone.get("type", "sprinkler")}
        if zone.get("minutes_remaining") is not None:
            attrs["minutes_remaining"] = zone["minutes_remaining"]
        if zone.get("opened_at"):
            attrs["opened_at"] = zone["opened_at"]
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        await self._client.send_zone_command(self._uid, self._zone_index, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self._client.send_zone_command(self._uid, self._zone_index, False)
        await self.coordinator.async_request_refresh()
