"""Base entity for DiyHome devices."""
from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DiyHomeCoordinator
from .const import DOMAIN

SUB_DEVICE_IRRIGATION = "irrigation"
SUB_DEVICE_TANK = "tank"


class DiyHomeEntity(CoordinatorEntity[DiyHomeCoordinator]):
    """Base class for DiyHome entities."""

    _attr_has_entity_name = True
    _sub_device: str | None = None  # None → device principale (valvole + stato)

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator)
        self._uid = uid

    @property
    def _device_data(self) -> dict:
        return self.coordinator.data.get(self._uid, {})

    @property
    def device_info(self):
        data = self._device_data
        main_name = data.get("name", "DiyHome WT-1")
        model = data.get("model", "DiyHome WT-1")
        main_id = (DOMAIN, self._uid)

        if self._sub_device is None:
            return {
                "identifiers": {main_id},
                "name": main_name,
                "model": model,
                "manufacturer": "DiyHome",
            }
        if self._sub_device == SUB_DEVICE_IRRIGATION:
            return {
                "identifiers": {(DOMAIN, f"{self._uid}_irrigation")},
                "name": "Irrigation",
                "model": model,
                "manufacturer": "DiyHome",
                "via_device": main_id,
            }
        # SUB_DEVICE_TANK
        return {
            "identifiers": {(DOMAIN, f"{self._uid}_tank")},
            "name": "Tank & Sensors",
            "model": model,
            "manufacturer": "DiyHome",
            "via_device": main_id,
        }
