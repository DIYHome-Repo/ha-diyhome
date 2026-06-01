"""Diagnostics support for DiyHome."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DiyHomeRuntimeData


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict:
    """Restituisce i dati diagnostici per questa config entry.

    Visibili in HA → Impostazioni → Dispositivi e servizi → DiyHome → Scarica diagnostica.
    Usati dagli utenti per allegare informazioni ai bug report.
    """
    runtime_data: DiyHomeRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator

    devices_diag = []
    for uid, device in (coordinator.data or {}).items():
        devices_diag.append(
            {
                "uid": uid,
                "name": device.get("name"),
                "online": device.get("online"),
                "model": device.get("model"),
                "firmware_version": device.get("firmwareVersion"),
                "alarm_active": device.get("alarm_active"),
                "has_valve1": device.get("valve1") is not None,
                "has_valve2": device.get("valve2") is not None,
                "valve1_open": (device.get("valve1") or {}).get("is_open"),
                "valve2_open": (device.get("valve2") or {}).get("is_open"),
                "zones_count": len(device.get("zones", [])),
                "active_zones": [
                    z.get("index")
                    for z in device.get("zones", [])
                    if z.get("is_active")
                ],
                "temp_sensors_count": len(device.get("temp_sensors", [])),
                "tank_level_pct": (device.get("tank") or {}).get("level_pct"),
                "has_flow_sensor": device.get("flow") is not None,
            }
        )

    return {
        "entry_id": entry.entry_id,
        "coordinator_last_update_success": coordinator.last_update_success,
        "coordinator_last_update": (
            coordinator.last_update_success_time.isoformat()
            if getattr(coordinator, "last_update_success_time", None)
            else None
        ),
        "devices_count": len(devices_diag),
        "devices": devices_diag,
    }
