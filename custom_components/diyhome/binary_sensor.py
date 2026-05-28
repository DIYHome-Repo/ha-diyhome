"""Binary sensor platform — online, allarme, zone attive."""
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
    entities: list[BinarySensorEntity] = []
    for uid, device in coordinator.data.items():
        entities.append(DiyHomeOnlineSensor(coordinator, uid))
        entities.append(DiyHomeAlarmSensor(coordinator, uid))
        entities.append(DiyHomeIrrigationActiveSensor(coordinator, uid))
        for zone in device.get("zones", []):
            entities.append(DiyHomeZoneActiveSensor(coordinator, uid, zone["index"], zone["name"]))
    async_add_entities(entities)


class DiyHomeOnlineSensor(DiyHomeEntity, BinarySensorEntity):
    """Connettività device — True = online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Online"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_online"
        self._attr_icon = "mdi:lan-connect"

    @property
    def is_on(self) -> bool:
        return self._device_data.get("online", False)


class DiyHomeAlarmSensor(DiyHomeEntity, BinarySensorEntity):
    """Allarme attivo — True = anomalia rilevata (perdita d'acqua, ecc.)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Allarme"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_alarm"
        self._attr_icon = "mdi:alert"

    @property
    def is_on(self) -> bool:
        return self._device_data.get("alarm_active", False)


class DiyHomeIrrigationActiveSensor(DiyHomeEntity, BinarySensorEntity):
    """Almeno una zona irrigazione è aperta."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Irrigazione attiva"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_irrigation_active"
        self._attr_icon = "mdi:sprinkler"

    @property
    def is_on(self) -> bool:
        return any(z.get("is_active", False) for z in self._device_data.get("zones", []))

    @property
    def available(self) -> bool:
        # FIX: include super().available → unavailable quando il coordinator fallisce
        return super().available and self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        active_zones = [
            z.get("name") or f"Zona {z.get('index', 0) + 1}"
            for z in self._device_data.get("zones", [])
            if z.get("is_active")
        ]
        return {"active_zones": active_zones, "count": len(active_zones)}


class DiyHomeZoneActiveSensor(DiyHomeEntity, BinarySensorEntity):
    """Stato attivo di una singola zona irrigazione."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str, zone_index: int, zone_name: str) -> None:
        super().__init__(coordinator, uid)
        self._zone_index = zone_index
        self._zone_name = zone_name
        self._attr_unique_id = f"{uid}_zone_{zone_index}_active"
        self._attr_icon = "mdi:sprinkler-variant"

    @property
    def name(self) -> str:
        zone = self._get_zone()
        return f"{zone.get('name') or self._zone_name} — Attiva"

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
        return {
            "zone_index": self._zone_index,
            "zone_type": zone.get("type", "sprinkler"),
            "minutes_remaining": zone.get("minutes_remaining"),
        }
