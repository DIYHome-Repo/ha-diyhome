"""DiyHome REST API client for Home Assistant."""
from __future__ import annotations

import aiohttp
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CLOUD_URL

_LOGGER = logging.getLogger(__name__)

API_BASE = f"{CLOUD_URL}/api/ha"
CLOUD_CALLBACK_URI = f"{CLOUD_URL}/api/ha/oauth/callback"


class DiyHomeApiClient:
    """Async REST client authenticated via Bearer token dalla config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    def _access_token(self) -> str:
        return self._entry.data["token"]["access_token"]

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str) -> dict:
        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                f"{API_BASE}{path}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status in (401, 403):
                raise ConfigEntryAuthFailed("DiyHome token non valido o scaduto")
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, json_data: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{API_BASE}{path}",
                headers=self._headers(),
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status in (401, 403):
                raise ConfigEntryAuthFailed("DiyHome token non valido o scaduto")
            resp.raise_for_status()
            return await resp.json()

    async def whoami(self) -> dict:
        """Diagnostica: restituisce userId, email e tutti i device."""
        return await self._get("/whoami")

    async def get_devices(self) -> dict:
        """Return list of devices with full state."""
        return await self._get("/devices")

    async def get_device_state(self, uid: str) -> dict:
        """Return state of a single device."""
        return await self._get(f"/devices/{uid}/state")

    async def send_command(self, uid: str, action: str) -> dict:
        """Send a valve command."""
        return await self._post(f"/devices/{uid}/command", {"action": action})

    async def send_zone_command(self, uid: str, zone_index: int, is_open: bool) -> dict:
        """Open or close an irrigation zone."""
        action = "zone_open" if is_open else "zone_close"
        return await self._post(f"/devices/{uid}/command", {
            "action": action,
            "zone_index": zone_index,
        })
