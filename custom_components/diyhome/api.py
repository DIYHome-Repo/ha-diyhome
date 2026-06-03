"""DiyHome API clients — Cloud REST e LAN HTTP."""
from __future__ import annotations

import logging
import uuid

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CLOUD_URL, LAN_CONNECT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

API_BASE = f"{CLOUD_URL}/api/ha"


# ─────────────────────────────────────────────────────────────────────────────
# Cloud API client — sessione riutilizzata per tutto il ciclo di vita
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeApiClient:
    """Client REST per il backend cloud DiyHome — autenticazione Bearer."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Restituisce la sessione condivisa, creandola se necessario."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict:
        token = self._entry.data.get("access_token", "")
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, path: str) -> dict:
        session = self._get_session()
        async with session.get(
            f"{API_BASE}{path}",
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (401, 403):
                raise ConfigEntryAuthFailed("DiyHome token non valido o scaduto")
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, json_data: dict) -> dict:
        session = self._get_session()
        async with session.post(
            f"{API_BASE}{path}",
            headers=self._headers(),
            json=json_data,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (401, 403):
                raise ConfigEntryAuthFailed("DiyHome token non valido o scaduto")
            resp.raise_for_status()
            return await resp.json()

    async def whoami(self) -> dict:
        return await self._get("/whoami")

    async def get_devices(self) -> dict:
        return await self._get("/devices")

    async def get_device_state(self, uid: str) -> dict:
        return await self._get(f"/devices/{uid}/state")

    async def send_command(self, uid: str, action: str, payload: dict | None = None) -> dict:
        """Invia comando al cloud con payload opzionale."""
        body: dict = {"action": action}
        if payload:
            body.update(payload)
        return await self._post(f"/devices/{uid}/command", body)

    async def send_zone_command(self, uid: str, zone_index: int, is_open: bool) -> dict:
        action = "zone_open" if is_open else "zone_close"
        return await self._post(
            f"/devices/{uid}/command",
            {"action": action, "zone_index": zone_index},
        )


# ─────────────────────────────────────────────────────────────────────────────
# LAN HTTP client — comunicazione diretta col device (zero cloud)
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeLanClient:
    """Client HTTP diretto al device in LAN — /api/v1/ha/* endpoint."""

    def __init__(self, ip: str) -> None:
        self.ip = ip.strip() if ip else ""
        self.session: aiohttp.ClientSession | None = None   # iniettata dal coordinator

    def _base(self) -> str:
        return f"http://{self.ip}"

    async def get_ha_state(self) -> dict:
        """GET /api/v1/ha/state — snapshot normalizzato per HA."""
        if not self.ip or not self.session:
            return {}
        async with self.session.get(
            f"{self._base()}/api/v1/ha/state",
            timeout=aiohttp.ClientTimeout(total=LAN_CONNECT_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_all_states(self) -> dict[str, dict] | None:
        """Scarica stato LAN e restituisce {uid: device_dict}.

        Ritorna None se il device non è raggiungibile.
        """
        try:
            raw = await self.get_ha_state()
            uid = raw.get("uid", "")
            if not uid:
                return None
            from . import _norm_lan_state  # importazione locale per evitare circolo
            return {uid: _norm_lan_state(raw)}
        except Exception as err:
            _LOGGER.debug("DiyHome LAN get_all_states: %s", err)
            return None

    async def send_command(self, action: str, payload: dict | None = None) -> bool:
        """POST /api/v1/command — stesso dispatcher del cloud, zero latenza.

        Genera un commandId locale per deduplicazione firmware.
        """
        if not self.ip or not self.session:
            return False
        body = {
            "commandId": str(uuid.uuid4()),
            "action":    action,
        }
        if payload:
            body.update(payload)
        try:
            async with self.session.post(
                f"{self._base()}/api/v1/command",
                json=body,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return bool(result.get("ok", False))
                return False
        except Exception as err:
            _LOGGER.debug("DiyHome LAN command error: %s", err)
            return False
