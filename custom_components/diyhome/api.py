"""DiyHome REST API client for Home Assistant."""
from __future__ import annotations

from homeassistant.helpers import config_entry_oauth2_flow

from .const import CLOUD_URL

API_BASE = f"{CLOUD_URL}/api/ha"


class DiyHomeApiClient:
    """Async REST client authenticated via OAuth2 Bearer token."""

    def __init__(self, session: config_entry_oauth2_flow.OAuth2Session) -> None:
        self._session = session

    async def _get(self, path: str) -> dict:
        resp = await self._session.async_request("GET", f"{API_BASE}{path}")
        resp.raise_for_status()
        return await resp.json()

    async def _post(self, path: str, json: dict) -> dict:
        resp = await self._session.async_request("POST", f"{API_BASE}{path}", json=json)
        resp.raise_for_status()
        return await resp.json()

    async def get_devices(self) -> dict:
        """Return list of devices with current state."""
        return await self._get("/devices")

    async def get_device_state(self, uid: str) -> dict:
        """Return state of a single device."""
        return await self._get(f"/devices/{uid}/state")

    async def send_command(self, uid: str, action: str) -> dict:
        """Send a command to a device (e.g. valve_open, valve_close)."""
        return await self._post(f"/devices/{uid}/command", {"action": action})
