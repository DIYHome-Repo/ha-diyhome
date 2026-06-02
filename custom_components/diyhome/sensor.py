"""Sensor platform — cisterna, temperatura, portata, consumo, zone, sensori multipli."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator, DiyHomeRuntimeData
from .entity import DiyHomeEntity, SUB_DEVICE_TANK, SUB_DEVICE_IRRIGATION

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class DiyHomeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | None] = field(default=lambda d: None)
    available_fn: Callable[[dict], bool] = field(default=lambda d: True)


# ── Sensori cisterna/portata — tank sub-device ─────────────────────────────
TANK_SENSOR_TYPES: tuple[DiyHomeSensorDescription, ...] = (
    DiyHomeSensorDescription(
        key="tank_level",
        translation_key="tank_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=lambda d: d.get("tank", {}).get("level_pct") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and d.get("tank") is not None,
    ),
    DiyHomeSensorDescription(
        key="tank_liters",
        translation_key="tank_liters",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water",
        value_fn=lambda d: d.get("tank", {}).get("liters") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and d.get("tank") is not None,
    ),
    DiyHomeSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda d: d.get("tank", {}).get("temperature") if d.get("tank") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("tank") is not None
            and d.get("tank", {}).get("temperature") is not None
        ),
    ),
    # ── Portata — diagnostica ──────────────────────────────────────────────
    DiyHomeSensorDescription(
        key="flow_in_rate",
        translation_key="flow_in_rate",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-pump",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("flow", {}).get("flow_in_rate") if d.get("flow") else None,
        available_fn=lambda d: d.get("online", False) and d.get("flow") is not None,
    ),
    DiyHomeSensorDescription(
        key="flow_out_rate",
        translation_key="flow_out_rate",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-pump",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("flow", {}).get("flow_out_rate") if d.get("flow") else None,
        available_fn=lambda d: d.get("online", False) and d.get("flow") is not None,
    ),
    # ── Consumo giornaliero — diagnostica ──────────────────────────────────
    DiyHomeSensorDescription(
        key="daily_consumption_in",
        translation_key="daily_consumption_in",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-plus",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            d.get("consumption_today", {}).get("liters_in")
            if d.get("consumption_today") else None
        ),
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("consumption_today") is not None
            and d.get("consumption_today", {}).get("liters_in") is not None
        ),
    ),
    DiyHomeSensorDescription(
        key="daily_consumption_out",
        translation_key="daily_consumption_out",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-minus",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            d.get("consumption_today", {}).get("liters_out")
            if d.get("consumption_today") else None
        ),
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("consumption_today") is not None
            and d.get("consumption_today", {}).get("liters_out") is not None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: DiyHomeRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    entities: list[SensorEntity] = []
    for uid, device in coordinator.data.items():
        for description in TANK_SENSOR_TYPES:
            entities.append(DiyHomeSensor(coordinator, uid, description))

        for ts in device.get("temp_sensors", []):
            entities.append(
                DiyHomeTempSensor(coordinator, uid, ts["address"], ts["name"])
            )

        for zone in device.get("zones", []):
            entities.append(
                DiyHomeZoneTimeSensor(coordinator, uid, zone["index"], zone["name"])
            )

    async_add_entities(entities)


class DiyHomeSensor(DiyHomeEntity, SensorEntity):
    """Sensore cisterna/portata — tank sub-device."""

    _sub_device = SUB_DEVICE_TANK
    entity_description: DiyHomeSensorDescription

    def __init__(
        self,
        coordinator: DiyHomeCoordinator,
        uid: str,
        description: DiyHomeSensorDescription,
    ) -> None:
        super().__init__(coordinator, uid)
        self.entity_description = description
        self._attr_unique_id = f"{uid}_{description.key}"

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self._device_data)

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(self._device_data)


class DiyHomeTempSensor(DiyHomeEntity, SensorEntity):
    """Sensore temperatura aggiuntivo (multi-sonda) — tank sub-device."""

    _sub_device = SUB_DEVICE_TANK
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        coordinator: DiyHomeCoordinator,
        uid: str,
        address: str,
        sensor_name: str,
    ) -> None:
        super().__init__(coordinator, uid)
        self._address = address
        self._sensor_name = sensor_name or address
        self._attr_unique_id = f"{uid}_temp_{address}"

    @property
    def name(self) -> str:
        return self._sensor_name

    def _get_sensor(self) -> dict:
        for ts in self._device_data.get("temp_sensors", []):
            if ts.get("address") == self._address:
                return ts
        return {}

    @property
    def native_value(self) -> float | None:
        return self._get_sensor().get("temp_c")

    @property
    def extra_state_attributes(self) -> dict:
        s = self._get_sensor()
        attrs: dict = {"address": self._address}
        if s.get("humidity") is not None:
            attrs["humidity"] = s["humidity"]
        return attrs

    @property
    def available(self) -> bool:
        return (
            super().available
            and self._device_data.get("online", False)
            and self._get_sensor().get("temp_c") is not None
        )


class DiyHomeZoneTimeSensor(DiyHomeEntity, SensorEntity):
    """Minuti rimanenti alla chiusura automatica di una zona — irrigation sub-device."""

    _sub_device = SUB_DEVICE_IRRIGATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "zone_timer"

    def __init__(
        self,
        coordinator: DiyHomeCoordinator,
        uid: str,
        zone_index: int,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator, uid)
        self._zone_index = zone_index
        self._zone_name = zone_name
        self._attr_unique_id = f"{uid}_zone_{zone_index}_remaining"

    @property
    def translation_placeholders(self) -> dict:
        zone = self._get_zone()
        name = zone.get("name") or self._zone_name or f"Zone {self._zone_index + 1}"
        return {"zone_name": name}

    def _get_zone(self) -> dict:
        for z in self._device_data.get("zones", []):
            if z.get("index") == self._zone_index:
                return z
        return {}

    @property
    def native_value(self) -> int | None:
        zone = self._get_zone()
        if not zone.get("is_active"):
            return 0
        return zone.get("minutes_remaining")

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        zone = self._get_zone()
        return {
            "zone_index": self._zone_index,
            "is_active": zone.get("is_active", False),
            "auto_close_at": zone.get("auto_close_at"),
        }
