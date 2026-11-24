"""DiyHome API clients — Cloud REST e LAN HTTP."""
from __future__ import annotations

import base64
import json as _jwt_json
import logging
import time as _jwt_time
import uuid

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CLOUD_URL, LAN_CONNECT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

API_BASE = f"{CLOUD_URL}/api/ha"


# ─────────────────────────────────────────────────────────────────────────────
# Mapping comandi HA → nomi firmware
# Il firmware usa valve_toggle+isOpen, zone_toggle+isActive, pump_mode+mode.
# L'API cloud accetta anche valve_open/close (backend fa il mapping), ma
# per i comandi LAN diretti al device serve il formato firmware nativo.
# ─────────────────────────────────────────────────────────────────────────────

def _map_lan_command(action: str, payload: dict | None) -> tuple[str, dict]:
    """Traduce comandi HA → nomi e payload che il firmware WT-1 accetta."""
    p = dict(payload or {})
    if action == "valve_open":
        return "valve_toggle", {"isOpen": True}
    if action == "valve_close":
        return "valve_toggle", {"isOpen": False}
    if action == "valve2_open":
        return "valve2_toggle", {"isOpen": True}
    if action == "valve2_close":
        return "valve2_toggle", {"isOpen": False}
    if action == "zone_open":
        zi = p.get("zone_index", p.get("zoneIndex", 0))
        return "zone_toggle", {"zoneIndex": int(zi), "isActive": True}
    if action == "zone_close":
        zi = p.get("zone_index", p.get("zoneIndex", 0))
        return "zone_toggle", {"zoneIndex": int(zi), "isActive": False}
    if action == "pump_enable":
        return "pump_mode", {"mode": "AUTO_ENABLE"}
    if action == "pump_disable":
        return "pump_mode", {"mode": "FORCED_DISABLED"}
    return action, p


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

    async def get_lan_jwt(self, uid: str) -> dict:
        """GET /api/ha/devices/:uid/lan-jwt — token JWT per comandi LAN autenticati."""
        return await self._get(f"/devices/{uid}/lan-jwt")

    async def send_command(self, uid: str, action: str, payload: dict | None = None) -> dict:
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
# LAN HTTP client — comunicazione diretta col device via hostname mDNS
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeLanClient:
    """Client HTTP diretto al device in LAN — usa hostname mDNS (stabile).

    L'hostname mDNS (es. "DIYHome_WT1_AABBCC.local") è fisso anche quando
    l'IP cambia dopo riavvio del router. Zero configurazione per l'utente.
    """

    def __init__(self, mdns_hostname: str) -> None:
        # mdns_hostname: già include ".local" (es. "DIYHome_WT1_AABBCC.local")
        # o è vuoto se non disponibile (cloud-only mode)
        self.mdns_hostname = mdns_hostname.strip() if mdns_hostname else ""
        self.session: aiohttp.ClientSession | None = None
        # Token JWT LAN (ES256) per autenticare i comandi HTTP al firmware
        # Fetchato dal cloud dopo setup, rinnovato automaticamente.
        # INT-011/INT-019: il token è legato a un solo device (claim `sub`):
        # si ricorda per quale uid è stato emesso e si invia solo a quel device.
        self._lan_token: str = ""
        self._lan_token_uid: str = ""
        # uid riportato dall'ultimo /api/v1/ha/state letto da questo host:
        # con più WT-1 solo questo device usa la LAN diretta, gli altri il cloud.
        self.device_uid: str = ""

    @property
    def ip(self) -> str:
        """Compatibilità retroattiva — ritorna l'hostname mDNS come 'indirizzo'."""
        return self.mdns_hostname

    def _base(self) -> str:
        return f"http://{self.mdns_hostname}"

    def is_available(self) -> bool:
        return bool(self.mdns_hostname)

    def serves(self, uid: str) -> bool:
        """True se l'host LAN configurato è il device `uid` (ultimo stato letto)."""
        return bool(uid) and self.is_available() and uid == self.device_uid

    def set_token(self, uid: str, token: str) -> None:
        self._lan_token = token
        self._lan_token_uid = uid if token else ""

    # ── JWT helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _jwt_expires_at(token: str) -> float:
        """Estrae campo `exp` dal JWT senza verifica firma.

        Ritorna timestamp Unix (float) oppure 0.0 su qualsiasi errore.
        """
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return 0.0
            padding = 4 - len(parts[1]) % 4
            b64 = parts[1] + ("=" * (padding % 4))
            decoded = base64.urlsafe_b64decode(b64.encode())
            return float(_jwt_json.loads(decoded).get("exp", 0))
        except Exception:
            return 0.0

    def needs_renewal(self, uid: str = "") -> bool:
        """True se il JWT LAN manca, è di un altro device o scade entro 24 ore.

        Chiamato in `async_send_command` prima di ogni comando LAN per
        rinnovare preventivamente il token — senza aspettare il 401 del
        firmware che causerebbe il fallback cloud silenzioso.
        """
        if not self._lan_token:
            return True
        if uid and uid != self._lan_token_uid:
            return True
        exp = self._jwt_expires_at(self._lan_token)
        if exp <= 0:
            return False  # token senza exp → non gestire (es. dev/test)
        return (_jwt_time.time() + 86_400) >= exp  # rinnova se scade entro 24h

    async def get_ha_state(self) -> dict:
        """GET /api/v1/ha/state — snapshot normalizzato per HA."""
        if not self.mdns_hostname or not self.session:
            return {}
        async with self.session.get(
            f"{self._base()}/api/v1/ha/state",
            timeout=aiohttp.ClientTimeout(total=LAN_CONNECT_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            raw = await resp.json()
        if isinstance(raw, dict) and raw.get("uid"):
            self.device_uid = str(raw["uid"])
        return raw

    async def get_all_states(self) -> dict[str, dict] | None:
        """Scarica stato LAN e restituisce {uid: device_dict}."""
        try:
            raw = await self.get_ha_state()
            uid = raw.get("uid", "")
            if not uid:
                return None
            from . import _norm_lan_state
            if raw.get("productTypeId", "wt1-v1") != "wt1-v1":
                return {}
            return {uid: _norm_lan_state(raw)}
        except Exception as err:
            _LOGGER.debug("DiyHome LAN get_all_states: %s", err)
            return None

    async def send_command(
        self, action: str, payload: dict | None = None, uid: str = ""
    ) -> bool:
        """POST /api/v1/command — zero latenza, zero cloud.

        Mappa automaticamente i comandi HA (valve_open/close) ai nomi firmware
        nativi (valve_toggle+isOpen) e aggiunge il token JWT LAN per l'auth.

        INT-011/INT-019: prima di inviare il comando (e il JWT) verifica che
        l'host risponda come il device `uid`; il JWT si invia solo se è stato
        emesso per quell'uid. Altrimenti False → il chiamante passa dal cloud.
        Limite: lo stato LAN non è autenticato, quindi un host ostile che
        dichiara lo stesso uid non viene escluso da questo controllo.
        """
        if not self.mdns_hostname or not self.session or not uid:
            return False
        try:
            state = await self.get_ha_state()
        except Exception as err:
            _LOGGER.debug("DiyHome LAN identità non verificata: %s", err)
            return False
        if not isinstance(state, dict) or state.get("uid") != uid:
            _LOGGER.warning(
                "DiyHome LAN: l'host %s non risponde come il device %s, "
                "comando inviato dal cloud",
                self.mdns_hostname, uid,
            )
            return False
        # Mappa al formato nativo firmware
        mapped_action, mapped_payload = _map_lan_command(action, payload)
        body = {
            "commandId": str(uuid.uuid4()),
            "action":    mapped_action,
        }
        body.update(mapped_payload)
        headers: dict = {}
        if self._lan_token and self._lan_token_uid == uid:
            headers["Authorization"] = f"Bearer {self._lan_token}"
        try:
            async with self.session.post(
                f"{self._base()}/api/v1/command",
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return bool(result.get("ok", False))
                _LOGGER.debug("DiyHome LAN command HTTP %s (action=%s)", resp.status, mapped_action)
                return False
        except Exception as err:
            _LOGGER.debug("DiyHome LAN command error: %s", err)
            return False
