"""Binary sensor platform — device online/offline."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    async_add_entities(
        DiyHomeOnlineSensor(coordinator, uid) for uid in coordinator.data
    )


class DiyHomeOnlineSensor(DiyHomeEntity, BinarySensorEntity):
    """Reports whether a DiyHome device is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Online"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_online"
        self._attr_icon = "mdi:lan-connect"

    @property
    def is_on(self) -> bool:
        return self._device_data.get("online", False)
