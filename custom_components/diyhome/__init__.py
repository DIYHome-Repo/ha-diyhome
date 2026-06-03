"""DiyHome integration for Home Assistant — v2.2.6 LAN-first (HTTP SSE + REST) + Cloud SSE sempre attiva."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiyHomeApiClient, DiyHomeLanClient
from .const import (
    CLOUD_SCAN_INTERVAL,
    CLOUD_URL,
    CONF_MDNS_HOSTNAME,
    DOMAIN,
    LAN_CONNECT_TIMEOUT,
    LAN_RETRY_INTERVAL,
    LAN_SCAN_INTERVAL,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

# ── Insiemi di eventi SSE accettati — tolleranti a naming variazioni ──────────
# FIX P4: cloud SSE accettava solo "device_update"; ora accetta tutti gli eventi
# che portano stato aggiornato del device, per robustezza futura.
_CLOUD_REALTIME_EVENTS: frozenset[str] = frozenset({
    "device_update",
    "ha_state",
    "state_update",
    "valve_update",
    "irrigation_update",
    "pump_update",
})

# FIX P5: LAN SSE accettava solo "ha_state"; ora accetta varianti firmware.
_LAN_REALTIME_EVENTS: frozenset[str] = frozenset({
    "ha_state",
    "device_update",
    "state_update",
    "state",
})


# ─────────────────────────────────────────────────────────────────────────────
# Normalizzatori payload → struttura dati attesa da sensor.py / switch.py
# Supportano sia il formato LAN (/api/v1/ha/state) che il cloud
# ─────────────────────────────────────────────────────────────────────────────

def _first_not_none(src: dict, *keys):
    """Ritorna il valore del primo tasto presente (incluso 0 e False)."""
    for k in keys:
        if k in src and src[k] is not None:
            return src[k]
    return None


def _norm_tank(payload: dict) -> dict:
    """Normalizza payload tank da qualsiasi sorgente → device['tank']."""
    temp = None
    ds18b20 = payload.get("ds18b20")
    if isinstance(ds18b20, dict):
        temp = _first_not_none(ds18b20, "tempC")
    if temp is None:
        temp = _first_not_none(payload, "temperature", "temp", "ambientTemp", "tempC")
    return {
        "level_pct":   _first_not_none(payload, "level_pct", "perc", "percentage", "level"),
        "liters":      _first_not_none(payload, "liters", "litri", "volume"),
        "temperature": temp,
    }


def _norm_flow(payload: dict) -> dict:
    """Normalizza payload flow da qualsiasi sorgente → device['flow']."""
    return {
        "flow_in_rate":  _first_not_none(
            payload, "flow_in_rate", "flowInRate_L_min", "in", "flowIn"
        ),
        "flow_out_rate": _first_not_none(
            payload, "flow_out_rate", "flowOutRate_L_min", "out", "flowOut"
        ),
    }


def _norm_diagnostics(existing: dict, payload: dict) -> dict:
    """Fonde payload diagnostics da qualsiasi sorgente → device['diagnostics']."""
    diag = dict(existing or {})
    wifi = payload.get("wifi", {}) if isinstance(payload.get("wifi"), dict) else {}
    updates = {
        "rssi":       _first_not_none(payload, "rssi", "wifiRSSI", "rssi_dbm")
                      or _first_not_none(wifi, "rssi"),
        "ssid":       _first_not_none(payload, "ssid", "wifiSSID")
                      or _first_not_none(wifi, "ssid"),
        "ip_address": _first_not_none(payload, "ip_address", "ip")
                      or _first_not_none(wifi, "ip"),
        "uptime":     _first_not_none(payload, "uptime", "uptime_s"),
        "heap_free":  _first_not_none(payload, "heap_free"),
    }
    diag.update({k: v for k, v in updates.items() if v is not None})
    return diag


def _norm_lan_state(raw: dict) -> dict:
    """Normalizza risposta /api/v1/ha/state (firmware LAN) → struttura coordinator.

    Il firmware restituisce già lo schema HA-normalizzato — qui convertiamo
    solo i pochi campi che possono differire (es. temperature come stringa).
    """
    device: dict = dict(raw)

    # Temperatura: il firmware può restituirla come stringa "22.5" o "--"
    tank = device.get("tank")
    if isinstance(tank, dict):
        temp = tank.get("temperature")
        if isinstance(temp, str):
            try:
                tank = dict(tank)
                tank["temperature"] = float(temp) if temp and temp != "--" else None
                device["tank"] = tank
            except ValueError:
                tank = dict(tank)
                tank["temperature"] = None
                device["tank"] = tank

    # alarms: da {any, low, high} → aggiungi alias alarm_active per binary_sensor
    alarms = device.get("alarms", {})
    if isinstance(alarms, dict):
        device["alarm_active"] = bool(alarms.get("any", False))

    # online: sempre True se riceviamo risposta LAN
    device["online"] = True

    return device


def _norm_cloud_state(raw: dict) -> dict:
    """Normalizza risposta cloud GET /api/ha/devices/{uid}/state → struttura coordinator."""
    device = dict(raw)
    alarms = device.get("alarms", {})
    if isinstance(alarms, dict):
        device["alarm_active"] = bool(alarms.get("any", False))
    return device


# ─────────────────────────────────────────────────────────────────────────────
# DiyHomeCoordinator — LAN-first con fallback cloud automatico
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeCoordinator(DataUpdateCoordinator):
    """Coordinator LAN-first:
      1. LAN SSE push  → /api/v1/ha/events    (sub-secondo, zero internet)
      2. LAN HTTP poll → /api/v1/ha/state     (watchdog 10s + first_refresh)
      3. LAN CMD       → /api/v1/command       (zero cloud per comandi)
      4. Cloud SSE     → /api/ha/stream        (fallback internet)
      5. Cloud REST    → /api/ha/devices       (emergenza 30s)

    Auto-switch: sonda LAN al boot con timeout 3s. Se risponde → LAN mode.
    Ogni LAN_RETRY_INTERVAL secondi riprova LAN se siamo in cloud mode.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: DiyHomeApiClient,
        lan_client: DiyHomeLanClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=CLOUD_SCAN_INTERVAL),
            always_update=False,
        )
        self.client = client
        self.lan_client = lan_client
        self._entry = entry
        self._online_states: dict[str, bool] = {}
        self._whoami_logged = False
        self._stopping = False

        # Modalità corrente
        self.lan_mode = False          # True = LAN SSE+HTTP attiva
        self._lan_sse_task: asyncio.Task | None = None
        self._lan_watchdog_task: asyncio.Task | None = None
        self._cloud_sse_task: asyncio.Task | None = None
        self._lan_retry_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        # FIX P5 anti-stale: timestamp dell'ultimo aggiornamento da sorgente LAN.
        # La cloud SSE ignora eventi arrivati entro 2s da un aggiornamento LAN
        # per evitare il race condition: cmd LAN → cloud SSE porta stato vecchio → rollback.
        self._lan_last_update: float = 0.0

    # ── Ciclo di vita ─────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Avvia il coordinator: sonda LAN, sceglie modalità."""
        # FIX P1: non ricreare la sessione se già esiste (creata in async_setup_entry
        # prima del primo refresh LAN — evita che get_all_states() riceva session=None)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self.lan_client.session = self._session

        lan_ok = await self._probe_lan()
        if lan_ok:
            await self._activate_lan_mode()
        else:
            await self._activate_cloud_mode()

    async def async_stop(self) -> None:
        """Ferma tutto in modo pulito."""
        self._stopping = True
        self._cancel_all_tasks()
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _update_from_lan(self, new_data: dict) -> None:
        """Aggiorna stato da sorgente LAN registrando il timestamp anti-stale."""
        self._lan_last_update = time.monotonic()
        self.async_set_updated_data(new_data)

    def _cancel_all_tasks(self) -> None:
        for task in (
            self._lan_sse_task,
            self._lan_watchdog_task,
            self._cloud_sse_task,
            self._lan_retry_task,
        ):
            if task and not task.done():
                task.cancel()
        self._lan_sse_task = None
        self._lan_watchdog_task = None
        self._cloud_sse_task = None
        self._lan_retry_task = None

    # ── Probe LAN ─────────────────────────────────────────────────────────────

    async def _probe_lan(self) -> bool:
        """Sonda il device via HTTP LAN (hostname mDNS). Ritorna True se raggiungibile."""
        if not self.lan_client.is_available():
            return False
        try:
            state = await asyncio.wait_for(
                self.lan_client.get_ha_state(), timeout=LAN_CONNECT_TIMEOUT
            )
            return isinstance(state, dict) and bool(state)
        except Exception:
            return False

    # ── Attivazione modalità ──────────────────────────────────────────────────

    async def _activate_lan_mode(self) -> None:
        """Avvia LAN SSE push + HTTP watchdog.

        La cloud SSE rimane SEMPRE attiva in parallelo — i comandi dall'app
        DiyHome o dal cloud arrivano via cloud SSE, non via LAN SSE firmware.
        Cancellare la cloud SSE causava aggiornamenti ritardati (solo watchdog 10s)
        quando il comando partiva dall'app invece che da HA stesso.
        """
        _LOGGER.info("DiyHome: modalità LAN attiva (%s)", self.lan_client.mdns_hostname)
        self.lan_mode = True
        self.update_interval = timedelta(seconds=LAN_SCAN_INTERVAL)

        # Cancella solo il retry LAN (non serve più: siamo già in LAN mode)
        if self._lan_retry_task and not self._lan_retry_task.done():
            self._lan_retry_task.cancel()
        self._lan_retry_task = None

        # Avvia LAN SSE
        if not self._lan_sse_task or self._lan_sse_task.done():
            self._lan_sse_task = self.hass.async_create_task(
                self._listen_lan_sse(),
                name=f"diyhome_lan_sse_{self._entry.entry_id}",
            )

        # Avvia watchdog HTTP
        if not self._lan_watchdog_task or self._lan_watchdog_task.done():
            self._lan_watchdog_task = self.hass.async_create_task(
                self._lan_watchdog(),
                name=f"diyhome_lan_watchdog_{self._entry.entry_id}",
            )

        # Cloud SSE: avvia/mantieni attiva — riceve aggiornamenti da comandi app/cloud
        if not self._cloud_sse_task or self._cloud_sse_task.done():
            self._cloud_sse_task = self.hass.async_create_task(
                self._listen_cloud_sse(),
                name=f"diyhome_cloud_sse_{self._entry.entry_id}",
            )

    async def _activate_cloud_mode(self) -> None:
        """Attiva cloud SSE + retry LAN periodico. Cancella LAN tasks."""
        _LOGGER.info("DiyHome: modalità cloud (SSE + REST poll 30s)")
        self.lan_mode = False
        self.update_interval = timedelta(seconds=CLOUD_SCAN_INTERVAL)

        # Cancella LAN
        for t in (self._lan_sse_task, self._lan_watchdog_task):
            if t and not t.done():
                t.cancel()
        self._lan_sse_task = None
        self._lan_watchdog_task = None

        # Avvia cloud SSE
        if not self._cloud_sse_task or self._cloud_sse_task.done():
            self._cloud_sse_task = self.hass.async_create_task(
                self._listen_cloud_sse(),
                name=f"diyhome_cloud_sse_{self._entry.entry_id}",
            )

        # Retry LAN periodico
        if self.lan_client.is_available() and (not self._lan_retry_task or self._lan_retry_task.done()):
            self._lan_retry_task = self.hass.async_create_task(
                self._lan_retry_loop(),
                name=f"diyhome_lan_retry_{self._entry.entry_id}",
            )

    # ── LAN SSE listener ──────────────────────────────────────────────────────

    async def _listen_lan_sse(self) -> None:
        """Long-running task: ascolta /api/v1/ha/events SSE dal device LAN."""
        url = f"http://{self.lan_client.mdns_hostname}/api/v1/ha/events"
        _LOGGER.debug("DiyHome LAN SSE: connessione a %s", url)

        while not self._stopping:
            try:
                async with self._session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=None, connect=LAN_CONNECT_TIMEOUT),
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug("DiyHome LAN SSE: HTTP %s, retry in 5s", resp.status)
                        await asyncio.sleep(5)
                        continue

                    _LOGGER.debug("DiyHome LAN SSE: connesso")
                    current_event: str | None = None
                    # FIX P6: line buffer — aiohttp restituisce chunk HTTP, non righe.
                    # Se il server invia "event: ha_state\ndata: {...}\n\n" in un unico
                    # res.write(), aiohttp lo consegna come un chunk singolo con \n embedded.
                    # Senza buffer, il parser vedeva "event: ha_state\ndata: {...}" come
                    # nome evento → current_event spazzatura → ogni evento SSE veniva scartato.
                    _sse_buf = b""

                    async for raw_chunk in resp.content:
                        if self._stopping:
                            return

                        _sse_buf += raw_chunk
                        while b"\n" in _sse_buf:
                            raw_line, _sse_buf = _sse_buf.split(b"\n", 1)
                            line = raw_line.decode("utf-8", errors="replace").strip()

                            if not line or line.startswith(":"):
                                current_event = None
                                continue

                            if line.startswith("event:"):
                                current_event = line[6:].strip()
                                continue

                            if line.startswith("data:") and current_event in _LAN_REALTIME_EVENTS:
                                try:
                                    payload = json.loads(line[5:].strip())
                                    uid = payload.get("uid")
                                    if uid and self.data and uid in self.data:
                                        # FIX P4: verifica payload completo prima di aggiornare.
                                        # Un payload parziale (es. solo valve1) sovrascriverebbe
                                        # tank, zones, flow, diagnostics con dati mancanti.
                                        _COMPLETE_KEYS = ("valve1", "tank", "zones", "pump", "flow")
                                        is_complete = any(k in payload for k in _COMPLETE_KEYS)
                                        if is_complete:
                                            new_data = dict(self.data)
                                            new_data[uid] = _norm_lan_state(payload)
                                            self._update_from_lan(new_data)
                                        else:
                                            # Payload parziale: ricarica stato completo dal firmware
                                            try:
                                                full = await self.lan_client.get_ha_state()
                                                if full:
                                                    new_data = dict(self.data)
                                                    new_data[uid] = _norm_lan_state({**full, "uid": uid})
                                                    self._update_from_lan(new_data)
                                            except Exception:
                                                pass
                                except Exception as parse_err:
                                    _LOGGER.debug("DiyHome LAN SSE parse: %s", parse_err)
                                current_event = None

            except asyncio.CancelledError:
                return
            except Exception as err:
                if self._stopping:
                    return
                _LOGGER.debug("DiyHome LAN SSE: errore (%s), retry in 1s", err)
                await asyncio.sleep(1)
                # Se LAN non risponde dopo disconnessione → torna cloud mode
                if not await self._probe_lan():
                    _LOGGER.info("DiyHome: LAN irraggiungibile, passaggio a cloud mode")
                    await self._activate_cloud_mode()
                    return

    # ── LAN HTTP watchdog ─────────────────────────────────────────────────────

    async def _lan_watchdog(self) -> None:
        """Watchdog HTTP: ri-sincronizza stato ogni LAN_SCAN_INTERVAL secondi.

        Serve come safety-net per catturare stati persi durante micro-disconnessioni
        SSE o quando nessun evento push è arrivato nell'intervallo.
        """
        while not self._stopping and self.lan_mode:
            await asyncio.sleep(LAN_SCAN_INTERVAL)
            if self._stopping or not self.lan_mode:
                return
            try:
                states = await self.lan_client.get_all_states()
                if states:
                    self._update_from_lan(states)
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.debug("DiyHome LAN watchdog: %s", err)

    # ── LAN retry loop ────────────────────────────────────────────────────────

    async def _lan_retry_loop(self) -> None:
        """Riprova connessione LAN ogni LAN_RETRY_INTERVAL secondi (in cloud mode)."""
        while not self._stopping and not self.lan_mode:
            await asyncio.sleep(LAN_RETRY_INTERVAL)
            if self._stopping or self.lan_mode:
                return
            _LOGGER.debug("DiyHome: retry probe LAN (%s)", self.lan_client.mdns_hostname)
            if await self._probe_lan():
                _LOGGER.info("DiyHome: LAN tornata disponibile, switch a LAN mode")
                await self._activate_lan_mode()
                return

    # ── Token refresh ─────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> bool:
        """Rinnova access_token usando il refresh_token salvato in entry.data.

        Chiama POST /api/ha/oauth/token con grant_type=refresh_token.
        Aggiorna entry.data con il nuovo access_token e refresh_token.
        Ritorna True se il refresh è andato a buon fine.
        """
        refresh_token = self._entry.data.get("refresh_token")
        if not refresh_token or not self._session:
            return False
        try:
            async with self._session.post(
                f"{CLOUD_URL}/api/ha/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("DiyHome token refresh: HTTP %s", resp.status)
                    return False
                data = await resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token", refresh_token)
                if not new_access:
                    return False
                # Aggiorna entry.data (api.py legge access_token da entry.data ad ogni chiamata)
                new_data = {**self._entry.data, "access_token": new_access, "refresh_token": new_refresh}
                self.hass.config_entries.async_update_entry(self._entry, data=new_data)
                return True
        except Exception as err:
            _LOGGER.debug("DiyHome token refresh errore: %s", err)
            return False

    # ── Cloud SSE listener ────────────────────────────────────────────────────

    async def _listen_cloud_sse(self) -> None:
        """Long-running task: ascolta /api/ha/stream SSE dal cloud DiyHome.

        Attiva SEMPRE — sia in modalità LAN che in modalità cloud.
        In LAN mode riceve aggiornamenti da comandi app/cloud DiyHome
        (il firmware non emette LAN SSE per comandi MQTT ricevuti).
        In cloud mode è la sorgente primaria di aggiornamenti real-time.
        """
        stream_url = f"{CLOUD_URL}/api/ha/stream"
        token = self._entry.data.get("access_token", "")
        headers = {"Authorization": f"Bearer {token}"}

        while not self._stopping:
            try:
                async with self._session.get(
                    stream_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, connect=15),
                ) as resp:
                    if resp.status in (401, 403):
                        _LOGGER.debug("DiyHome cloud SSE: token scaduto (HTTP %s), tentativo refresh", resp.status)
                        refreshed = await self._refresh_access_token()
                        if refreshed:
                            headers = {"Authorization": f"Bearer {self._entry.data.get('access_token', '')}"}
                            _LOGGER.debug("DiyHome cloud SSE: token rinnovato, riconnessione")
                        else:
                            _LOGGER.warning("DiyHome cloud SSE: refresh token fallito, retry tra 60s")
                            await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        _LOGGER.debug("DiyHome cloud SSE: HTTP %s, retry 5s", resp.status)
                        await asyncio.sleep(5)
                        continue

                    _LOGGER.debug("DiyHome cloud SSE: connesso (lan_mode=%s)", self.lan_mode)
                    current_event: str | None = None
                    # FIX P6: stessa correzione del LAN SSE — line buffer per gestire
                    # chunk HTTP multi-riga inviati in un unico res.write() dal backend.
                    _sse_buf = b""

                    async for raw_chunk in resp.content:
                        if self._stopping:
                            return

                        _sse_buf += raw_chunk
                        while b"\n" in _sse_buf:
                            raw_line, _sse_buf = _sse_buf.split(b"\n", 1)
                            line = raw_line.decode("utf-8", errors="replace").strip()

                            if not line or line.startswith(":"):
                                current_event = None
                                continue

                            if line.startswith("event:"):
                                current_event = line[6:].strip()
                                continue

                            if line.startswith("data:") and current_event in _CLOUD_REALTIME_EVENTS:
                                try:
                                    # FIX P5 anti-stale: se un update LAN è avvenuto < 2s fa,
                                    # l'evento cloud potrebbe portare stato precedente al comando
                                    # (race: HA→LAN cmd → cloud SSE ritardato con stato vecchio).
                                    if self.lan_mode and (time.monotonic() - self._lan_last_update) < 2.0:
                                        current_event = None
                                        continue
                                    payload = json.loads(line[5:].strip())
                                    uid = payload.get("uid")
                                    if uid and self.data and uid in self.data:
                                        embedded = payload.get("state")
                                        if embedded:
                                            new_data = dict(self.data)
                                            new_data[uid] = _norm_cloud_state(embedded)
                                            self.async_set_updated_data(new_data)
                                        else:
                                            try:
                                                state = await self.client.get_device_state(uid)
                                                new_data = dict(self.data)
                                                new_data[uid] = _norm_cloud_state(state)
                                                self.async_set_updated_data(new_data)
                                            except ConfigEntryAuthFailed:
                                                return
                                            except Exception:
                                                await self.async_request_refresh()
                                except Exception:
                                    pass
                                current_event = None

            except asyncio.CancelledError:
                return
            except Exception as err:
                if self._stopping:
                    return
                _LOGGER.debug("DiyHome cloud SSE: errore (%s), retry 5s", err)
                await asyncio.sleep(5)

    # ── REST polling safety-net ───────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Polling periodico come safety-net finale.

        In LAN mode: HTTP GET /api/v1/ha/state — intervallo 10s (watchdog).
        In cloud mode: REST GET /api/ha/devices — intervallo 30s (emergenza).
        """
        try:
            if self.lan_mode:
                states = await self.lan_client.get_all_states()
                if states:
                    # Aggiorna online states per log
                    for uid, device in states.items():
                        online = device.get("online", False)
                        was = self._online_states.get(uid)
                        if was is True and not online:
                            _LOGGER.warning("DiyHome %s andato offline", uid)
                        elif was is False and online:
                            _LOGGER.info("DiyHome %s tornato online", uid)
                    self._online_states = {u: d.get("online", False) for u, d in states.items()}
                    return states
                # LAN non risponde: torna cloud mode
                _LOGGER.info("DiyHome: LAN watchdog fallita, switch cloud mode")
                await self._activate_cloud_mode()

            # Cloud mode
            if not self._whoami_logged:
                try:
                    w = await self.client.whoami()
                    _LOGGER.debug("DiyHome whoami: userId=%s", w.get("userId"))
                except Exception:
                    pass
                self._whoami_logged = True

            data = await self.client.get_devices()
            devices = {
                d["uid"]: _norm_cloud_state(d)
                for d in data.get("devices", [])
                if d.get("uid")
            }
            for uid, device in devices.items():
                online = device.get("online", False)
                was = self._online_states.get(uid)
                if was is True and not online:
                    _LOGGER.warning("DiyHome device %s offline", uid)
                elif was is False and online:
                    _LOGGER.info("DiyHome device %s tornato online", uid)
            self._online_states = {u: d.get("online", False) for u, d in devices.items()}
            return devices

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"DiyHome update error: {err}") from err

    # ── Token LAN JWT ─────────────────────────────────────────────────────────

    async def _fetch_lan_jwt(self, uid: str) -> None:
        """Fetcha (o rinnova) il JWT LAN dal cloud e lo inietta nel lan_client.

        Chiamato al setup e in _activate_lan_mode se il token sta per scadere.
        Il token è ES256 firmato con la chiave OTA già nel firmware — il device
        può verificarlo senza un nuovo provisioning.
        """
        try:
            result = await self.client.get_lan_jwt(uid)
            token = result.get("token", "")
            if token:
                self.lan_client._lan_token = token
                _LOGGER.debug("DiyHome: LAN JWT ottenuto per %s (exp=%s)", uid, result.get("expires_at"))
        except Exception as err:
            _LOGGER.debug("DiyHome: LAN JWT fetch fallito per %s: %s", uid, err)

    # ── Comandi ───────────────────────────────────────────────────────────────

    async def async_send_command(
        self, uid: str, action: str, payload: dict | None = None
    ) -> bool:
        """Invia comando: LAN diretta se disponibile, cloud altrimenti."""
        if self.lan_mode and self.lan_client.is_available():
            try:
                ok = await self.lan_client.send_command(action, payload or {})
                if ok:
                    # P8: dopo comando LAN riuscito, aggiorna subito stato HA da LAN.
                    # _update_from_lan registra anche il timestamp anti-stale (FIX P5)
                    # così la cloud SSE ignora l'eventuale echo ritardato con stato vecchio.
                    try:
                        states = await self.lan_client.get_all_states()
                        if states:
                            self._update_from_lan(states)
                    except Exception:
                        pass
                    return True
                _LOGGER.debug("DiyHome LAN command fallito, fallback cloud")
            except Exception as err:
                _LOGGER.debug("DiyHome LAN command error (%s), fallback cloud", err)

        # Cloud fallback
        cmd_payload: dict = {"action": action}
        if payload:
            cmd_payload.update(payload)
        await self.client.send_command(uid, action, payload or {})
        return True


# Alias retrocompat
DualModeCoordinator = DiyHomeCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# Runtime data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiyHomeRuntimeData:
    coordinator: DiyHomeCoordinator
    client: DiyHomeApiClient


# ─────────────────────────────────────────────────────────────────────────────
# Setup / Unload
# ─────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DiyHome from a config entry — LAN-first via mDNS hostname."""
    client = DiyHomeApiClient(hass, entry)

    # Hostname mDNS — presente se device scoperto via zeroconf o config manuale
    # Es: "DIYHome_WT1_AABBCC.local" — stabile anche dopo riavvio router
    mdns_hostname = (
        entry.data.get(CONF_MDNS_HOSTNAME, "")
        or entry.options.get(CONF_MDNS_HOSTNAME, "")
    ).strip()

    lan_client = DiyHomeLanClient(mdns_hostname=mdns_hostname)
    coordinator = DiyHomeCoordinator(hass, client, lan_client, entry)

    # FIX P1: crea la sessione HTTP PRIMA del primo tentativo LAN.
    # Senza questo, get_all_states() trovava session=None e restituiva {}
    # silenziosamente, rendendo l'integrazione non davvero LAN-first.
    if mdns_hostname:
        coordinator._session = aiohttp.ClientSession()
        lan_client.session = coordinator._session

    # First refresh: prova LAN via mDNS hostname prima, poi cloud
    if mdns_hostname:
        try:
            states = await asyncio.wait_for(
                lan_client.get_all_states(), timeout=LAN_CONNECT_TIMEOUT
            )
            if states:
                coordinator.async_set_updated_data(states)
                coordinator.lan_mode = True
                _LOGGER.info("DiyHome: first refresh da LAN (%s)", mdns_hostname)
        except Exception:
            pass

    if not coordinator.data:
        # Nessun dato LAN: carica da cloud
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise ConfigEntryNotReady(f"DiyHome non raggiungibile: {err}") from err

    runtime_data = DiyHomeRuntimeData(coordinator=coordinator, client=client)
    entry.runtime_data = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # FIX P2: token LAN fetchato PRIMA di async_start(), così è disponibile
    # dal primo momento in cui il coordinator inizia ad accettare comandi LAN.
    if mdns_hostname and coordinator.data:
        for uid in list(coordinator.data.keys()):
            await coordinator._fetch_lan_jwt(uid)

    # Avvia coordinator (SSE + watchdog + retry)
    # async_start() non ricrea la sessione se già esiste (FIX P1 cooperante)
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data: DiyHomeRuntimeData = entry.runtime_data
    await runtime_data.coordinator.async_stop()
    # FIX P7: chiudi la sessione del cloud client per evitare resource leak
    await runtime_data.client.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
