"""Base entity for DiyHome devices."""
from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DiyHomeCoordinator
from .const import DOMAIN


class DiyHomeEntity(CoordinatorEntity[DiyHomeCoordinator]):
    """Base class for DiyHome entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator)
        self._uid = uid

    @property
    def _device_data(self) -> dict:
        return self.coordinator.data.get(self._uid, {})

    @property
    def device_info(self):
        data = self._device_data
        return {
            "identifiers": {(DOMAIN, self._uid)},
            "name": data.get("name", "DiyHome Device"),
            "model": data.get("model", "DiyHome WT-1"),
            "manufacturer": "DiyHome",
        }
