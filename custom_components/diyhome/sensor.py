"""Sensor platform — livello cisterna, temperatura, litri."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DiyHomeCoordinator
from .const import DOMAIN
from .entity import DiyHomeEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiyHomeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | None] = lambda d: None
    available_fn: Callable[[dict], bool] = lambda d: True


SENSOR_TYPES: tuple[DiyHomeSensorDescription, ...] = (
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
        available_fn=lambda d: d.get("online", False) and d.get("tank", {}).get("temperature") is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DiyHomeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for uid in coordinator.data:
        for description in SENSOR_TYPES:
            entities.append(DiyHomeSensor(coordinator, uid, description))

    async_add_entities(entities)


class DiyHomeSensor(DiyHomeEntity, SensorEntity):
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
    def name(self) -> str:
        return self.entity_description.name

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self._device_data)

    @property
    def available(self) -> bool:
        return self.entity_description.available_fn(self._device_data)
