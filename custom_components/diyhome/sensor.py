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
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfVolume, UnitOfVolumeFlowRate, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator
from .const import DOMAIN
from .entity import DiyHomeEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiyHomeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | None] = field(default=lambda d: None)
    available_fn: Callable[[dict], bool] = field(default=lambda d: True)


SENSOR_TYPES: tuple[DiyHomeSensorDescription, ...] = (
    # ── Cisterna ──────────────────────────────────────────────────────────────
    DiyHomeSensorDescription(
        key="tank_level",
        name="Livello cisterna",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
        value_fn=lambda d: d.get("tank", {}).get("level_pct") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and d.get("tank") is not None,
    ),
    DiyHomeSensorDescription(
        key="tank_liters",
        name="Volume cisterna",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water",
        value_fn=lambda d: d.get("tank", {}).get("liters") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and d.get("tank") is not None,
    ),
    DiyHomeSensorDescription(
        key="temperature",
        name="Temperatura",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda d: d.get("tank", {}).get("temperature") if d.get("tank") else None,
        available_fn=lambda d: d.get("online", False) and bool(d.get("tank", {}).get("temperature")),
    ),
    # ── Portata ───────────────────────────────────────────────────────────────
    DiyHomeSensorDescription(
        key="flow_in_rate",
        name="Portata entrata",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-pump",
        value_fn=lambda d: d.get("flow", {}).get("flow_in_rate") if d.get("flow") else None,
        available_fn=lambda d: d.get("online", False) and d.get("flow") is not None,
    ),
    DiyHomeSensorDescription(
        key="flow_out_rate",
        name="Portata uscita",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-pump",
        value_fn=lambda d: d.get("flow", {}).get("flow_out_rate") if d.get("flow") else None,
        available_fn=lambda d: d.get("online", False) and d.get("flow") is not None,
    ),
    # ── Consumo giornaliero ───────────────────────────────────────────────────
    DiyHomeSensorDescription(
        key="daily_consumption_in",
        name="Consumo oggi (ingresso)",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-plus",
        value_fn=lambda d: d.get("consumption_today", {}).get("liters_in", 0),
        available_fn=lambda d: True,
    ),
    DiyHomeSensorDescription(
        key="daily_consumption_out",
        name="Consumo oggi (uscita)",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:water-minus",
        value_fn=lambda d: d.get("consumption_today", {}).get("liters_out", 0),
        available_fn=lambda d: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DiyHomeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []
    for uid, device in coordinator.data.items():
        # Sensori base (sempre creati)
        for description in SENSOR_TYPES:
            entities.append(DiyHomeSensor(coordinator, uid, description))

        # Sensori temperatura aggiuntivi (temperatura_sensors multi-sonda)
        for ts in device.get("temp_sensors", []):
            entities.append(DiyHomeTempSensor(coordinator, uid, ts["address"], ts["name"]))

        # Sensore "tempo rimanente irrigazione" per ogni zona attiva
        for zone in device.get("zones", []):
            entities.append(DiyHomeZoneTimeSensor(coordinator, uid, zone["index"], zone["name"]))

    async_add_entities(entities)


class DiyHomeSensor(DiyHomeEntity, SensorEntity):
    entity_description: DiyHomeSensorDescription

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str, description: DiyHomeSensorDescription) -> None:
        super().__init__(coordinator, uid)
        self.entity_description = description
        self._attr_unique_id = f"{uid}_{description.key}"

    @property
    def name(self) -> str:
        return self.entity_description.name

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self._device_data)

    @property
    def available(self) -> bool:
        return self.entity_description.available_fn(self._device_data)


class DiyHomeTempSensor(DiyHomeEntity, SensorEntity):
    """Sensore temperatura aggiuntivo (multi-sonda)."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str, address: str, sensor_name: str) -> None:
        super().__init__(coordinator, uid)
        self._address = address
        self._sensor_name = sensor_name or address
        self._attr_unique_id = f"{uid}_temp_{address}"

    @property
    def name(self) -> str:
        return f"Temperatura {self._sensor_name}"

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
        return self._device_data.get("online", False) and self._get_sensor().get("temp_c") is not None


class DiyHomeZoneTimeSensor(DiyHomeEntity, SensorEntity):
    """Minuti rimanenti alla chiusura automatica di una zona irrigazione."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str, zone_index: int, zone_name: str) -> None:
        super().__init__(coordinator, uid)
        self._zone_index = zone_index
        self._zone_name = zone_name
        self._attr_unique_id = f"{uid}_zone_{zone_index}_remaining"

    @property
    def name(self) -> str:
        zone = self._get_zone()
        return f"{zone.get('name') or self._zone_name} — Tempo rimanente"

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
        return self._device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict:
        zone = self._get_zone()
        return {
            "zone_index": self._zone_index,
            "is_active": zone.get("is_active", False),
            "auto_close_at": zone.get("auto_close_at"),
        }
