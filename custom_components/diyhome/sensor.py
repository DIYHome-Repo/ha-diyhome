"""Sensor platform — cisterna, temperatura, portata, consumo, diagnostica, pompa."""
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
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator, DiyHomeRuntimeData
from .entity import DiyHomeEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


@dataclass(frozen=True)
class DiyHomeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | None] = field(default=lambda d: None)
    available_fn: Callable[[dict], bool] = field(default=lambda d: True)


# ── Sensori principali (Sensors section) ──────────────────────────────────────
MAIN_SENSOR_TYPES: tuple[DiyHomeSensorDescription, ...] = (
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
        state_class=SensorStateClass.TOTAL,
        icon="mdi:water",
        value_fn=lambda d: d.get("tank", {}).get("liters") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and d.get("tank") is not None,
    ),
    DiyHomeSensorDescription(
        key="tank_volume_m3",
        translation_key="tank_volume_m3",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:water",
        value_fn=lambda d: d.get("tank", {}).get("m3") if d.get("tank") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("tank") is not None
            and d.get("tank", {}).get("m3") is not None
        ),
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
    DiyHomeSensorDescription(
        key="flow_in_rate",
        translation_key="flow_in_rate",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-pump",
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
        value_fn=lambda d: d.get("flow", {}).get("flow_out_rate") if d.get("flow") else None,
        available_fn=lambda d: d.get("online", False) and d.get("flow") is not None,
    ),
    DiyHomeSensorDescription(
        key="flow_in_total",
        translation_key="flow_in_total",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.get("flow", {}).get("flow_in_total") if d.get("flow") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("flow") is not None
            and d.get("flow", {}).get("flow_in_total") is not None
        ),
    ),
    DiyHomeSensorDescription(
        key="flow_out_total",
        translation_key="flow_out_total",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=lambda d: d.get("flow", {}).get("flow_out_total") if d.get("flow") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("flow") is not None
            and d.get("flow", {}).get("flow_out_total") is not None
        ),
    ),
    DiyHomeSensorDescription(
        key="daily_consumption_in",
        translation_key="daily_consumption_in",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-plus",
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
    DiyHomeSensorDescription(
        key="monthly_consumption_in",
        translation_key="monthly_consumption_in",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-plus",
        value_fn=lambda d: (
            d.get("consumption_monthly", {}).get("liters_in")
            if d.get("consumption_monthly") else None
        ),
        available_fn=lambda d: d.get("online", False) and d.get("consumption_monthly") is not None,
    ),
    DiyHomeSensorDescription(
        key="monthly_consumption_out",
        translation_key="monthly_consumption_out",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-minus",
        value_fn=lambda d: (
            d.get("consumption_monthly", {}).get("liters_out")
            if d.get("consumption_monthly") else None
        ),
        available_fn=lambda d: d.get("online", False) and d.get("consumption_monthly") is not None,
    ),
    DiyHomeSensorDescription(
        key="monthly_consumption_total",
        translation_key="monthly_consumption_total",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:water",
        value_fn=lambda d: (
            d.get("consumption_monthly", {}).get("liters_total")
            if d.get("consumption_monthly") else None
        ),
        available_fn=lambda d: d.get("online", False) and d.get("consumption_monthly") is not None,
    ),
    DiyHomeSensorDescription(
        key="forecast_month_l",
        translation_key="forecast_month_l",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-timeline-variant",
        value_fn=lambda d: d.get("forecast", {}).get("month_l") if d.get("forecast") else None,
        available_fn=lambda d: d.get("online", False) and d.get("forecast") is not None,
    ),
    DiyHomeSensorDescription(
        key="forecast_cost_month",
        translation_key="forecast_cost_month",
        native_unit_of_measurement="EUR",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
        value_fn=lambda d: d.get("forecast", {}).get("cost_month") if d.get("forecast") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("forecast") is not None
            and d.get("forecast", {}).get("cost_month") is not None
        ),
    ),
)

# ── Sensori diagnostici (Diagnostics section) ─────────────────────────────────
DIAGNOSTIC_SENSOR_TYPES: tuple[DiyHomeSensorDescription, ...] = (
    DiyHomeSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
        value_fn=lambda d: d.get("diagnostics", {}).get("rssi"),
        available_fn=lambda d: d.get("diagnostics", {}).get("rssi") is not None,
    ),
    DiyHomeSensorDescription(
        key="uptime",
        translation_key="uptime",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:clock-outline",
        value_fn=lambda d: d.get("diagnostics", {}).get("uptime"),
        available_fn=lambda d: d.get("diagnostics", {}).get("uptime") is not None,
    ),
    DiyHomeSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=lambda d: d.get("firmware"),
        available_fn=lambda d: d.get("firmware") is not None,
    ),
    DiyHomeSensorDescription(
        key="wifi_network",
        translation_key="wifi_network",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi-settings",
        value_fn=lambda d: d.get("diagnostics", {}).get("ssid") or None,
        available_fn=lambda d: bool(d.get("diagnostics", {}).get("ssid")),
    ),
    DiyHomeSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ip-network",
        value_fn=lambda d: d.get("diagnostics", {}).get("ip_address") or None,
        available_fn=lambda d: bool(d.get("diagnostics", {}).get("ip_address")),
    ),
    DiyHomeSensorDescription(
        key="pump_mode",
        translation_key="pump_mode",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:pump",
        value_fn=lambda d: d.get("pump", {}).get("mode") if d.get("pump") else None,
        available_fn=lambda d: d.get("pump") is not None,
    ),
    DiyHomeSensorDescription(
        key="tank_distance",
        translation_key="tank_distance",
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ruler",
        value_fn=lambda d: d.get("tank", {}).get("distance_cm") if d.get("tank") else None,
        available_fn=lambda d: (
            d.get("online", False)
            and d.get("tank") is not None
            and d.get("tank", {}).get("distance_cm") is not None
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
        for description in MAIN_SENSOR_TYPES:
            entities.append(DiyHomeSensor(coordinator, uid, description))

        for description in DIAGNOSTIC_SENSOR_TYPES:
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
    """Sensore generico basato su description."""

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
    """Sensore temperatura aggiuntivo (multi-sonda DS18B20)."""

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
            and bool(self._get_sensor())
        )


class DiyHomeZoneTimeSensor(DiyHomeEntity, SensorEntity):
    """Minuti rimanenti alla chiusura automatica di una zona — diagnostica."""

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
