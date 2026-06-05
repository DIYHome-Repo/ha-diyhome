"""Binary sensor platform — online, allarme, irrigazione attiva, relè pompa."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator, DiyHomeRuntimeData
from .entity import DiyHomeEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: DiyHomeRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[BinarySensorEntity] = []
    for uid, device in coordinator.data.items():
        entities.append(DiyHomeOnlineSensor(coordinator, uid))
        entities.append(DiyHomeAlarmSensor(coordinator, uid))
        entities.append(DiyHomeLeakSensor(coordinator, uid))
        entities.append(DiyHomeIrrigationActiveSensor(coordinator, uid))
        if device.get("pump") is not None:
            entities.append(DiyHomePumpRelaySensor(coordinator, uid))
        if device.get("valve1") is not None:
            entities.append(DiyHomeValveProtectionSensor(coordinator, uid, valve_num=1))
        if device.get("valve2") is not None:
            entities.append(DiyHomeValveProtectionSensor(coordinator, uid, valve_num=2))
        for alarm in device.get("alarms", []):
            entities.append(
                DiyHomeAlarmBinarySensor(coordinator, uid, alarm["id"], alarm.get("type", ""))
            )
    async_add_entities(entities)


class DiyHomeOnlineSensor(DiyHomeEntity, BinarySensorEntity):
    """Connettività device — device principale, diagnostica."""

    _attr_translation_key = "online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_online"

    @property
    def is_on(self) -> bool:
        return self._device_data.get("online", False)


class DiyHomeAlarmSensor(DiyHomeEntity, BinarySensorEntity):
    """Allarme attivo — anomalia rilevata. Device principale."""

    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_alarm"

    @property
    def is_on(self) -> bool:
        return self._device_data.get("alarm_active", False)


class DiyHomeIrrigationActiveSensor(DiyHomeEntity, BinarySensorEntity):
    """Almeno una zona irrigazione è aperta."""

    _attr_translation_key = "irrigation_active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:sprinkler"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_irrigation_active"

    @property
    def is_on(self) -> bool:
        return any(
            z.get("is_active", False) for z in self._device_data.get("zones", [])
        )

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        active_zones = [
            z.get("name") or f"Zone {z.get('index', 0) + 1}"
            for z in self._device_data.get("zones", [])
            if z.get("is_active")
        ]
        return {"active_zones": active_zones, "count": len(active_zones)}


class DiyHomePumpRelaySensor(DiyHomeEntity, BinarySensorEntity):
    """Stato fisico relè pompa — diagnostica."""

    _attr_translation_key = "pump_relay"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_pump_relay"

    @property
    def is_on(self) -> bool:
        pump = self._device_data.get("pump", {})
        return bool(pump.get("relay_on", False))

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("pump") is not None

    @property
    def extra_state_attributes(self) -> dict:
        pump = self._device_data.get("pump", {})
        return {
            "mode": pump.get("mode"),
            "is_locked": pump.get("is_locked", False),
        }


class DiyHomeLeakSensor(DiyHomeEntity, BinarySensorEntity):
    """Perdita d'acqua rilevata — evento leak non risolto presente."""

    _attr_translation_key = "leak_detected"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_icon = "mdi:pipe-leak"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{uid}_leak_detected"

    @property
    def is_on(self) -> bool:
        return bool(self._device_data.get("leak_active", False))


class DiyHomeValveProtectionSensor(DiyHomeEntity, BinarySensorEntity):
    """Protezione anti-sovraccarico valvola attiva."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-check"

    def __init__(
        self, coordinator: DiyHomeCoordinator, uid: str, valve_num: int = 1
    ) -> None:
        super().__init__(coordinator, uid)
        self._valve_num = valve_num
        self._attr_translation_key = (
            "valve1_protection" if valve_num == 1 else "valve2_protection"
        )
        self._attr_unique_id = f"{uid}_valve{valve_num}_protection"

    @property
    def is_on(self) -> bool:
        valve = self._device_data.get(f"valve{self._valve_num}") or {}
        return bool(valve.get("protection_enabled", False))

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._device_data.get(f"valve{self._valve_num}") is not None
        )

    @property
    def extra_state_attributes(self) -> dict:
        valve = self._device_data.get(f"valve{self._valve_num}") or {}
        return {"protection_time": valve.get("protection_time")}


class DiyHomeAlarmBinarySensor(DiyHomeEntity, BinarySensorEntity):
    """Singola regola allarme — attiva/inattiva per soglia configurata."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-circle"

    def __init__(
        self,
        coordinator: DiyHomeCoordinator,
        uid: str,
        alarm_id: int,
        alarm_type: str,
    ) -> None:
        super().__init__(coordinator, uid)
        self._alarm_id = alarm_id
        self._alarm_type = alarm_type
        self._attr_unique_id = f"{uid}_alarm_{alarm_id}"
        self._attr_name = f"Alarm {alarm_type.replace('_', ' ').title()}"

    @property
    def is_on(self) -> bool:
        for a in self._device_data.get("alarms", []):
            if a.get("id") == self._alarm_id:
                return bool(a.get("active", False))
        return False

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        for a in self._device_data.get("alarms", []):
            if a.get("id") == self._alarm_id:
                return {
                    "type":      a.get("type"),
                    "threshold": a.get("threshold"),
                    "enabled":   a.get("enabled"),
                }
        return {}
