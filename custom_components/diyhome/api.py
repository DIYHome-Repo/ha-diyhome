"""DiyHome REST API client for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

from .const import CLOUD_URL

_LOGGER = logging.getLogger(__name__)

API_BASE = f"{CLOUD_URL}/api/ha"


class DiyHomeApiClient:
    """Async REST client — usa OAuth2Session per token auto-refresh."""

    def __init__(self, session: OAuth2Session) -> None:
        self._session = session

    async def _get(self, path: str) -> dict:
        resp = await self._session.async_request(
            "GET",
            f"{API_BASE}{path}",
        )
        if resp.status in (401, 403):
            raise ConfigEntryAuthFailed("DiyHome token non valido o scaduto")
        resp.raise_for_status()
        return await resp.json()

    async def _post(self, path: str, json_data: dict) -> dict:
        resp = await self._session.async_request(
            "POST",
            f"{API_BASE}{path}",
            json=json_data,
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
        return await self._post(
            f"/devices/{uid}/command",
            {"action": action, "zone_index": zone_index},
        )
