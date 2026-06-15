"""Switch platform — valvola principale, valvola 2, zone irrigazione, pompa."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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

    entities: list[SwitchEntity] = []
    for uid, device in coordinator.data.items():
        entities.append(DiyHomeValveSwitch(coordinator, uid, valve=1))
        if device.get("valve2") is not None:
            entities.append(DiyHomeValveSwitch(coordinator, uid, valve=2))
        for zone in device.get("zones", []):
            entities.append(
                DiyHomeZoneSwitch(coordinator, uid, zone["index"], zone.get("name", ""))
            )
        if device.get("pump") is not None:
            entities.append(DiyHomePumpSwitch(coordinator, uid))

    async_add_entities(entities)


# ─────────────────────────────────────────────────────────────────────────────
# Valvola
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeValveSwitch(DiyHomeEntity, SwitchEntity):
    """Valvola DiyHome — device principale."""

    _attr_icon = "mdi:valve"
    _attr_translation_key = "valve"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str, valve: int) -> None:
        super().__init__(coordinator, uid)
        self._coordinator = coordinator
        self._valve = valve
        self._attr_unique_id = f"{uid}_valve{valve}"
        self._optimistic_is_on: bool | None = None

    @property
    def translation_placeholders(self) -> dict:
        return {"number": str(self._valve)}

    @property
    def name(self) -> str | None:
        valve_data = self._device_data.get(f"valve{self._valve}", {})
        return valve_data.get("name") or None

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        valve_data = self._device_data.get(f"valve{self._valve}")
        if valve_data is None:
            return None
        return valve_data.get("is_open", False)

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("online", False)

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_is_on is not None:
            # Cancella l'ottimistico SOLO quando il dato reale del coordinator
            # conferma lo stato target — evita il rimbalzo causato da aggiornamenti
            # cloud SSE che arrivano con lo stato vecchio prima della conferma LAN.
            valve_data = self._device_data.get(f"valve{self._valve}")
            real_is_on = (
                valve_data.get("is_open", False)
                if isinstance(valve_data, dict)
                else None
            )
            if real_is_on is not None and real_is_on == self._optimistic_is_on:
                self._optimistic_is_on = None
            # else: mantieni ottimistico — il firmware non ha ancora aggiornato
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        valve_data = self._device_data.get(f"valve{self._valve}") or {}
        attrs: dict = {}
        if valve_data.get("type") is not None:
            attrs["valve_type"] = valve_data["type"]
        if valve_data.get("protection_enabled") is not None:
            attrs["protection_enabled"] = valve_data["protection_enabled"]
        if valve_data.get("protection_time") is not None:
            attrs["protection_time"] = valve_data["protection_time"]
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        self._optimistic_is_on = True
        self.async_write_ha_state()
        action = "valve2_open" if self._valve == 2 else "valve_open"
        await self._coordinator.async_send_command(self._uid, action)

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_is_on = False
        self.async_write_ha_state()
        action = "valve2_close" if self._valve == 2 else "valve_close"
        await self._coordinator.async_send_command(self._uid, action)


# ─────────────────────────────────────────────────────────────────────────────
# Zone irrigazione
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeZoneSwitch(DiyHomeEntity, SwitchEntity):
    """Zona irrigazione."""

    _attr_icon = "mdi:sprinkler-variant"

    def __init__(
        self,
        coordinator: DiyHomeCoordinator,
        uid: str,
        zone_index: int,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator, uid)
        self._coordinator = coordinator
        self._zone_index = zone_index
        self._zone_name = zone_name
        self._attr_unique_id = f"{uid}_zone_{zone_index}"
        self._optimistic_is_on: bool | None = None

    @property
    def name(self) -> str:
        zone = self._get_zone()
        return zone.get("name") or self._zone_name or f"Zone {self._zone_index + 1}"

    def _get_zone(self) -> dict:
        for z in self._device_data.get("zones", []):
            if z.get("index") == self._zone_index:
                return z
        return {}

    @property
    def is_on(self) -> bool:
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        return self._get_zone().get("is_active", False)

    @property
    def available(self) -> bool:
        return super().available and self._device_data.get("online", False)

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_is_on is not None:
            real_is_on = self._get_zone().get("is_active")
            if real_is_on is not None and real_is_on == self._optimistic_is_on:
                self._optimistic_is_on = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        zone = self._get_zone()
        attrs: dict = {
            "zone_index": self._zone_index,
            "zone_type": zone.get("type", "sprinkler"),
        }
        if zone.get("minutes_remaining") is not None:
            attrs["minutes_remaining"] = zone["minutes_remaining"]
        if zone.get("opened_at"):
            attrs["opened_at"] = zone["opened_at"]
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        self._optimistic_is_on = True
        self.async_write_ha_state()
        await self._coordinator.async_send_command(
            self._uid, "zone_open", {"zone_index": self._zone_index}
        )

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_is_on = False
        self.async_write_ha_state()
        await self._coordinator.async_send_command(
            self._uid, "zone_close", {"zone_index": self._zone_index}
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pompa
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomePumpSwitch(DiyHomeEntity, SwitchEntity):
    """Abilita/disabilita pompa (AUTO_ENABLE ↔ FORCED_DISABLED)."""

    _attr_translation_key = "pump_enabled"
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: DiyHomeCoordinator, uid: str) -> None:
        super().__init__(coordinator, uid)
        self._coordinator = coordinator
        self._attr_unique_id = f"{uid}_pump_enabled"
        self._optimistic_is_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        pump = self._device_data.get("pump")
        if pump is None:
            return None
        return pump.get("mode") == "AUTO_ENABLE"

    @property
    def available(self) -> bool:
        pump = self._device_data.get("pump")
        if pump is None:
            return False
        return (
            super().available
            and self._device_data.get("online", False)
            and pump.get("mode") not in ("SERVICE_MODE", "PUMP_LOCKED")
        )

    def _handle_coordinator_update(self) -> None:
        if self._optimistic_is_on is not None:
            pump = self._device_data.get("pump")
            real_is_on = (
                (pump.get("mode") == "AUTO_ENABLE")
                if isinstance(pump, dict) and pump.get("mode") is not None
                else None
            )
            if real_is_on is not None and real_is_on == self._optimistic_is_on:
                self._optimistic_is_on = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        pump = self._device_data.get("pump", {})
        return {
            "mode": pump.get("mode"),
            "is_locked": pump.get("is_locked", False),
            "relay_on": pump.get("relay_on"),
            "lan_mode": self._coordinator.lan_mode,
        }

    async def async_turn_on(self, **kwargs) -> None:
        self._optimistic_is_on = True
        self.async_write_ha_state()
        await self._coordinator.async_send_command(self._uid, "pump_enable")

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_is_on = False
        self.async_write_ha_state()
        await self._coordinator.async_send_command(self._uid, "pump_disable")
