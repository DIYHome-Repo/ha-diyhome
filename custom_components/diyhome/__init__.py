"""DiyHome integration for Home Assistant — v2.4.0 entità estese: m³, total flow, monthly, forecast, leak, valve protection, alarms, cpu/heap diag."""
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiyHomeApiClient, DiyHomeLanClient
from .const import (
    CLOUD_SCAN_INTERVAL,
    CLOUD_URL,
    CONF_MDNS_HOSTNAME,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
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
    liters = _first_not_none(payload, "liters", "litri", "volume")
    m3_val = _first_not_none(payload, "m3")
    if m3_val is None and liters is not None:
        try:
            m3_val = round(float(liters) / 1000, 4)
        except (TypeError, ValueError):
            pass
    return {
        "level_pct":   _first_not_none(payload, "level_pct", "perc", "percentage", "level"),
        "liters":      liters,
        "m3":          m3_val,
        "temperature": temp,
        "distance_cm": _first_not_none(payload, "distance_cm", "distanceCm", "dist_cm"),
    }


def _norm_flow(payload: dict) -> dict:
    """Normalizza payload flow da qualsiasi sorgente → device['flow']."""
    return {
        "flow_in_rate":   _first_not_none(
            payload, "flow_in_rate", "flowInRate_L_min", "in", "flowIn"
        ),
        "flow_out_rate":  _first_not_none(
            payload, "flow_out_rate", "flowOutRate_L_min", "out", "flowOut"
        ),
        "flow_in_total":  _first_not_none(
            payload, "flow_in_total", "flowInTotal", "totalIn"
        ),
        "flow_out_total": _first_not_none(
            payload, "flow_out_total", "flowOutTotal", "totalOut"
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
        "free_heap":  _first_not_none(payload, "free_heap", "freeHeap", "heap_free"),
        "cpu_temp":   _first_not_none(payload, "cpu_temp", "cpuTemp", "cpu_temperature"),
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
        device.setdefault("leak_active", device["alarm_active"])
    # normalization m3 e distance_cm per tank (se firmware non li espone già)
    tank = device.get("tank")
    if isinstance(tank, dict):
        tank = dict(tank)
        if tank.get("m3") is None and tank.get("liters") is not None:
            try:
                tank["m3"] = round(float(tank["liters"]) / 1000, 4)
            except (TypeError, ValueError):
                pass
        device["tank"] = tank

    # online: sempre True se riceviamo risposta LAN
    device["online"] = True

    return device


def _norm_cloud_state(raw: dict) -> dict:
    """Normalizza risposta cloud GET /api/ha/devices/{uid}/state → struttura coordinator."""
    device = dict(raw)
    alarms = device.get("alarms")
    if isinstance(alarms, dict):
        # compatibilità formato vecchio: {any, low, high}
        device["alarm_active"] = bool(alarms.get("any", False))
        device.setdefault("leak_active", device["alarm_active"])
    elif isinstance(alarms, list):
        # nuovo formato REST: [{id, type, threshold, enabled, active}, ...]
        if "alarm_active" not in device:
            device["alarm_active"] = any(a.get("active", False) for a in alarms)
        device.setdefault("leak_active", device.get("alarm_active", False))
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

        # DualModeCoordinator: MQTT locale (Ondata #28)
        self._mqtt_task: asyncio.Task | None = None
        self.mqtt_connected: bool = False
        self._mqtt_pub: object | None = None  # aiomqtt.Client per publish comandi

    # ── Ciclo di vita ─────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Avvia il coordinator: sonda LAN, sceglie modalità."""
        # FIX P1: non ricreare la sessione se già esiste (creata in async_setup_entry
        # prima del primo refresh LAN — evita che get_all_states() riceva session=None)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self.lan_client.session = self._session

        # FIX L1 (Bug A): se async_setup_entry ha già confermato LAN mode con una
        # GET riuscita (lan_mode=True), non ri-sondare. Il double probe causava:
        #   probe1 OK → setup entità (5-20s) → probe2 FAIL (device HTTP occupato,
        #   mDNS lento su Docker) → _activate_cloud_mode() → sovrascrive lan_mode=True
        #   → retry loop attende 60s → utente vede "cloud" per 60s anche se in LAN.
        if self.lan_mode:
            _LOGGER.debug(
                "DiyHome: async_start: LAN mode già confermato da setup, skip re-probe"
            )
            await self._activate_lan_mode()
            return

        lan_ok = await self._probe_lan()
        if lan_ok:
            await self._activate_lan_mode()
        else:
            await self._activate_cloud_mode()

        # Avvia MQTT locale se configurato (indipendente da LAN/cloud mode)
        mqtt_host = (
            self._entry.options.get(CONF_MQTT_HOST)
            or self._entry.data.get(CONF_MQTT_HOST, "")
        ).strip()
        if mqtt_host:
            mqtt_port = int(
                self._entry.options.get(CONF_MQTT_PORT)
                or self._entry.data.get(CONF_MQTT_PORT, 1883)
            )
            mqtt_user = (
                self._entry.options.get(CONF_MQTT_USERNAME)
                or self._entry.data.get(CONF_MQTT_USERNAME, "")
            ).strip()
            mqtt_pass = (
                self._entry.options.get(CONF_MQTT_PASSWORD)
                or self._entry.data.get(CONF_MQTT_PASSWORD, "")
            )
            if not self._mqtt_task or self._mqtt_task.done():
                self._mqtt_task = self.hass.async_create_task(
                    self._mqtt_loop(mqtt_host, mqtt_port, mqtt_user, mqtt_pass),
                    name=f"diyhome_mqtt_{self._entry.entry_id}",
                )
                _LOGGER.info(
                    "DiyHome: MQTT locale configurato (%s:%d) — avvio loop",
                    mqtt_host, mqtt_port,
                )

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
            self._mqtt_task,
        ):
            if task and not task.done():
                task.cancel()
        self._lan_sse_task = None
        self._lan_watchdog_task = None
        self._cloud_sse_task = None
        self._lan_retry_task = None
        self._mqtt_task = None
        self.mqtt_connected = False
        self._mqtt_pub = None

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

        # Retry LAN periodico — parte SEMPRE in cloud mode (anche se hostname vuoto:
        # _lan_retry_loop usa zeroconf per scoprire il device automaticamente)
        if not self._lan_retry_task or self._lan_retry_task.done():
            self._lan_retry_task = self.hass.async_create_task(
                self._lan_retry_loop(),
                name=f"diyhome_lan_retry_{self._entry.entry_id}",
            )

    # ── LAN SSE listener ──────────────────────────────────────────────────────

    async def _listen_lan_sse(self) -> None:
        """Long-running task: ascolta /api/v1/ha/events SSE dal device LAN.

        FIX P9 (dead TCP): aggiunto sock_read=25s. Se non arrivano dati in 25s
        (evento o keepalive firmware), aiohttp solleva TimeoutError → reconnect.
        Il firmware invia un keepalive "ping" ogni 15s per mantenere la TCP viva
        attraverso i NAT router (che chiudono silenziosamente connessioni idle).
        Senza keepalive, il router chiude la TCP, firmware crede il client ancora
        connesso, haPushState() scrive su socket morto → HACS non riceve nulla.
        """
        url = f"http://{self.lan_client.mdns_hostname}/api/v1/ha/events"
        _LOGGER.debug("DiyHome LAN SSE: connessione a %s", url)

        while not self._stopping:
            try:
                async with self._session.get(
                    url,
                    # sock_read=25: timeout se nessun dato (evento o keepalive) arriva
                    # in 25s. Il firmware invia keepalive ogni 15s → in condizioni normali
                    # il timeout non scatta mai. Se la TCP è morta (router NAT timeout),
                    # scatta entro 25s e force-reconnect.
                    timeout=aiohttp.ClientTimeout(total=None, connect=LAN_CONNECT_TIMEOUT, sock_read=25),
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.debug("DiyHome LAN SSE: HTTP %s, retry in 5s", resp.status)
                        await asyncio.sleep(5)
                        continue

                    _LOGGER.debug("DiyHome LAN SSE: connesso a %s", self.lan_client.mdns_hostname)
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
                                            _LOGGER.debug(
                                                "DiyHome LAN SSE: evento '%s' uid=%s → HA aggiornato",
                                                current_event, uid,
                                            )
                                        else:
                                            # Payload parziale: ricarica stato completo dal firmware
                                            _LOGGER.debug(
                                                "DiyHome LAN SSE: payload parziale uid=%s, ricarico stato", uid
                                            )
                                            try:
                                                full = await self.lan_client.get_ha_state()
                                                if full:
                                                    new_data = dict(self.data)
                                                    new_data[uid] = _norm_lan_state({**full, "uid": uid})
                                                    self._update_from_lan(new_data)
                                            except Exception:
                                                pass
                                    else:
                                        _LOGGER.debug(
                                            "DiyHome LAN SSE: uid=%s non in self.data (keys=%s)",
                                            uid, list(self.data.keys()) if self.data else "None",
                                        )
                                except Exception as parse_err:
                                    _LOGGER.debug("DiyHome LAN SSE parse: %s", parse_err)
                                current_event = None

            except asyncio.CancelledError:
                return
            except Exception as err:
                if self._stopping:
                    return
                # TimeoutError = nessun dato in 25s (TCP morta o firmware non invia keepalive)
                # Reconnect immediato: il firmware invia lo stato corrente via onConnect()
                if isinstance(err, asyncio.TimeoutError):
                    _LOGGER.debug("DiyHome LAN SSE: timeout 25s (TCP inattiva) — reconnect")
                else:
                    _LOGGER.debug("DiyHome LAN SSE: errore (%s) — retry in 1s", err)
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

    def _extract_ip_from_cloud_data(self) -> str:
        """Estrae l'IP del device dai dati cloud (diagnostics.ip_address).

        Usato come fallback quando zeroconf non è disponibile (HA in Docker/container).
        Il cloud popola questo campo dal shadow/reported del device (network_configs).
        """
        if not self.data:
            return ""
        for device in self.data.values():
            if not isinstance(device, dict):
                continue
            diag = device.get("diagnostics") or {}
            ip = diag.get("ip_address") or ""
            if ip and ip not in ("0.0.0.0", ""):
                _LOGGER.info("DiyHome: IP estratto dai dati cloud → %s", ip)
                return ip
        return ""

    async def _lan_retry_loop(self) -> None:
        """Riprova connessione LAN ogni LAN_RETRY_INTERVAL secondi (in cloud mode).

        Se mdns_hostname è vuoto (setup senza IP, zeroconf non trovato al boot),
        prova prima zeroconf, poi estrae l'IP dai dati cloud come fallback.
        Quando trovato: aggiorna entry.data per persistere tra riavvii HA.

        FIX L2 (Bug B): primo tentativo dopo 5s (non 60s).
        Se la causa è un probe transitorio fallito in async_start() (device
        momentaneamente occupato), riproviamo subito invece di aspettare 60s.
        I tentativi successivi usano LAN_RETRY_INTERVAL (60s) per non sovraccaricare.
        """
        first_attempt = True
        while not self._stopping and not self.lan_mode:
            await asyncio.sleep(5 if first_attempt else LAN_RETRY_INTERVAL)
            first_attempt = False
            if self._stopping or self.lan_mode:
                return

            # Se non abbiamo ancora un hostname, proviamo a scoprire il device
            if not self.lan_client.mdns_hostname:
                discovered = await self._zeroconf_discover_hostname()
                # Fallback: IP dai dati cloud (funziona anche in Docker/container
                # dove zeroconf non risolve .local)
                if not discovered:
                    discovered = self._extract_ip_from_cloud_data()
                if discovered:
                    _LOGGER.info(
                        "DiyHome: device scoperto via zeroconf (background) → %s — switch a LAN mode",
                        discovered,
                    )
                    self.lan_client.mdns_hostname = discovered
                    # Persisti in entry.data per sopravvivere a riavvii HA
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={**self._entry.data, CONF_MDNS_HOSTNAME: discovered},
                    )
                    # Crea sessione HTTP se non ancora esistente
                    if self._session is None or self._session.closed:
                        self._session = aiohttp.ClientSession()
                        self.lan_client.session = self._session

            _LOGGER.debug("DiyHome: retry probe LAN (%s)", self.lan_client.mdns_hostname)
            if await self._probe_lan():
                _LOGGER.info("DiyHome: LAN tornata disponibile, switch a LAN mode")
                await self._activate_lan_mode()
                return

    async def _zeroconf_discover_hostname(self) -> str:
        """Scan zeroconf per _diyhome._tcp.local. (max 5s) in background.

        Abbina il device per UID TXT record con i device del coordinator.
        Se coordinator.data è vuoto (raro), accetta il primo device DiyHome trovato.
        Ritorna hostname .local se trovato, "" altrimenti. Graceful su qualsiasi errore.
        """
        known_uids: set[str] = set(self.data.keys()) if self.data else set()

        try:
            from homeassistant.components.zeroconf import async_get_async_instance
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

            aiozc = await async_get_async_instance(self.hass)
            found_names: list[str] = []
            evt = asyncio.Event()

            def _handler(
                zeroconf_instance,
                service_type: str,
                name: str,
                state_change: ServiceStateChange,
            ) -> None:
                if state_change in (
                    ServiceStateChange.Added,
                    ServiceStateChange.Updated,
                ):
                    if name not in found_names:
                        found_names.append(name)
                    evt.set()

            browser = AsyncServiceBrowser(
                aiozc.zeroconf,
                "_diyhome._tcp.local.",
                handlers=[_handler],
            )
            try:
                await asyncio.wait_for(evt.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            finally:
                browser.cancel()

            for name in found_names:
                info = AsyncServiceInfo("_diyhome._tcp.local.", name)
                try:
                    if await info.async_request(aiozc.zeroconf, 3000):
                        props = info.properties or {}
                        uid_bytes = props.get(b"uid") or props.get("uid", b"")
                        uid = (
                            uid_bytes.decode("utf-8", errors="replace")
                            if isinstance(uid_bytes, bytes)
                            else str(uid_bytes)
                        )
                        # Accetta se UID è nel nostro account, oppure se non abbiamo UIDs
                        if not known_uids or uid in known_uids:
                            # Preferisce IP diretto su .local — in HA Docker/container
                            # il resolver mDNS può non funzionare per nomi .local
                            addrs = info.parsed_addresses() or []
                            ipv4 = next((a for a in addrs if ":" not in a), None)
                            return ipv4 or (info.server.rstrip(".") if info.server else "")
                except Exception:
                    continue

        except Exception as err:
            _LOGGER.debug("DiyHome: zeroconf background scan: %s", err)

        return ""

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
                    # sock_read=30: se non arrivano dati (evento o keepalive ": ping")
                    # entro 30s, la TCP è morta (NAT timeout, proxy idle close) →
                    # TimeoutError → reconnect. Il server invia keepalive ogni 25s,
                    # quindi in condizioni normali il timeout non scatta mai.
                    # FIX P9-cloud: identico alla LAN SSE sock_read=25.
                    timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=30),
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

                    _LOGGER.debug(
                        "DiyHome cloud SSE: connesso (lan_mode=%s, url=%s)",
                        self.lan_mode, stream_url,
                    )
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

                            # FIX v2.3.3: su (ri)connessione SSE forza refresh immediato.
                            # Il backend manda "event: connected" + push stato device subito dopo.
                            # Il refresh qui garantisce che anche se il push arriva leggermente
                            # in ritardo, HA abbia lo stato corretto entro pochi secondi.
                            if line.startswith("data:") and current_event == "connected":
                                _LOGGER.debug(
                                    "DiyHome cloud SSE: connessione (ri)stabilita → refresh immediato"
                                )
                                self.hass.async_create_task(self.async_request_refresh())
                                current_event = None
                                continue

                            if line.startswith("data:") and current_event in _CLOUD_REALTIME_EVENTS:
                                try:
                                    payload = json.loads(line[5:].strip())
                                    uid = payload.get("uid")
                                    if uid and self.data and uid in self.data:
                                        embedded = payload.get("state")
                                        if self.lan_mode:
                                            # FIX v2.3.4: in LAN mode l'anti-stale (age<2s) bloccava
                                            # TUTTI gli eventi cloud app→HA quando il firmware manda
                                            # LAN SSE periodicamente (sub-secondo) → _lan_last_update
                                            # sempre recente → cloud SSE sempre scartata → latenza 10-30s.
                                            #
                                            # Nuovo comportamento: usa embedded state del cloud subito
                                            # (fast-path ottimistico), poi conferma con LAN GET dopo
                                            # 400ms (tempo al device di eseguire il comando MQTT).
                                            if embedded:
                                                new_data = dict(self.data)
                                                new_data[uid] = _norm_cloud_state(embedded)
                                                self.async_set_updated_data(new_data)
                                                _LOGGER.debug(
                                                    "DiyHome cloud SSE LAN: evento '%s' uid=%s → aggiornato embedded, verifica LAN in 400ms",
                                                    current_event, uid,
                                                )
                                            # Conferma asincrona con stato reale del device
                                            async def _confirm_from_lan(uid: str = uid) -> None:
                                                await asyncio.sleep(0.4)
                                                try:
                                                    full = await self.lan_client.get_ha_state()
                                                    if full and self.data and uid in self.data:
                                                        new_data = dict(self.data)
                                                        new_data[uid] = _norm_lan_state({**full, "uid": uid})
                                                        self._update_from_lan(new_data)
                                                        _LOGGER.debug(
                                                            "DiyHome cloud SSE LAN: conferma LAN uid=%s → ok",
                                                            uid,
                                                        )
                                                except Exception as _e:
                                                    _LOGGER.debug(
                                                        "DiyHome cloud SSE LAN: conferma LAN uid=%s fallita: %s",
                                                        uid, _e,
                                                    )
                                            self.hass.async_create_task(_confirm_from_lan())
                                        else:
                                            # Cloud mode: usa embedded state direttamente
                                            if embedded:
                                                new_data = dict(self.data)
                                                new_data[uid] = _norm_cloud_state(embedded)
                                                self.async_set_updated_data(new_data)
                                                _LOGGER.debug(
                                                    "DiyHome cloud SSE: evento '%s' uid=%s → HA aggiornato",
                                                    current_event, uid,
                                                )
                                            else:
                                                _LOGGER.debug(
                                                    "DiyHome cloud SSE: evento '%s' uid=%s senza stato embedded → GET",
                                                    current_event, uid,
                                                )
                                                try:
                                                    state = await self.client.get_device_state(uid)
                                                    new_data = dict(self.data)
                                                    new_data[uid] = _norm_cloud_state(state)
                                                    self.async_set_updated_data(new_data)
                                                except ConfigEntryAuthFailed:
                                                    return
                                                except Exception:
                                                    await self.async_request_refresh()
                                    else:
                                        _LOGGER.debug(
                                            "DiyHome cloud SSE: uid=%s non in self.data", uid
                                        )
                                except Exception:
                                    pass
                                current_event = None

            except asyncio.CancelledError:
                return
            except Exception as err:
                if self._stopping:
                    return
                # TimeoutError = nessun dato in 30s (TCP morta, NAT timeout, proxy idle-close)
                # Reconnect immediato: il server manderà subito un "connected" + stato attuale.
                if isinstance(err, asyncio.TimeoutError):
                    _LOGGER.debug("DiyHome cloud SSE: timeout 30s (TCP inattiva) — reconnect immediato")
                else:
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

    async def _confirm_lan_after_command(self, uid: str) -> None:
        """Ricarica stato LAN 350ms dopo un comando riuscito.

        FIX I6: il GET immediato (vecchio comportamento P8) restituiva lo stato
        PRECEDENTE perché il firmware esegue i comandi in modo asincrono (task
        FreeRTOS). Attendere 350ms garantisce che il device abbia già mutato
        lo stato prima del GET di conferma.
        _update_from_lan aggiorna anche il timestamp anti-stale (FIX P5).
        """
        await asyncio.sleep(0.35)
        try:
            states = await self.lan_client.get_all_states()
            if states and self.data:
                new_data = {**self.data, **states}
                self._update_from_lan(new_data)
                _LOGGER.debug("DiyHome: conferma LAN post-cmd uid=%s → ok", uid)
        except Exception as err:
            _LOGGER.debug("DiyHome: conferma LAN post-cmd uid=%s fallita: %s", uid, err)

    # ── DualModeCoordinator: MQTT locale ─────────────────────────────────────

    @staticmethod
    def _action_to_mqtt(uid: str, action: str, extra: dict | None) -> tuple[str | None, dict]:
        """Mappa action HA → (topic MQTT device, payload). Ritorna (None, {}) se non mappato."""
        base = f"diyhome/{uid}"
        _MAP: dict[str, tuple[str, dict]] = {
            "valve_open":   (f"{base}/valve1/set", {"state": "open"}),
            "valve_close":  (f"{base}/valve1/set", {"state": "close"}),
            "valve2_open":  (f"{base}/valve2/set", {"state": "open"}),
            "valve2_close": (f"{base}/valve2/set", {"state": "close"}),
            "pump_enable":  (f"{base}/pump/set",   {"action": "enable"}),
            "pump_disable": (f"{base}/pump/set",   {"action": "disable"}),
        }
        if action not in _MAP:
            return None, {}
        topic, base_payload = _MAP[action]
        merged = {**base_payload, **(extra or {})}
        return topic, merged

    async def _mqtt_loop(
        self, host: str, port: int, username: str, password: str
    ) -> None:
        """Loop riconnessione al broker MQTT locale — aiomqtt come primary source."""
        try:
            import aiomqtt  # noqa: PLC0415
        except ImportError:
            _LOGGER.error(
                "DiyHome: pacchetto aiomqtt non trovato — "
                "reinstalla l'integrazione per abilitare MQTT locale"
            )
            return

        retry_delay = 5
        while not self._stopping:
            try:
                kwargs: dict = {"hostname": host, "port": port, "timeout": 10}
                if username:
                    kwargs["username"] = username
                    kwargs["password"] = password

                async with aiomqtt.Client(**kwargs) as mqtt_client:
                    self._mqtt_pub = mqtt_client
                    self.mqtt_connected = True
                    retry_delay = 5  # reset backoff
                    _LOGGER.info(
                        "DiyHome MQTT: connesso a %s:%d", host, port
                    )

                    # Sottoscrivi ai topic di ogni device conosciuto
                    known_uids = list(self.data.keys()) if self.data else []
                    if known_uids:
                        for dev_uid in known_uids:
                            await mqtt_client.subscribe(
                                f"diyhome/{dev_uid}/shadow/reported", qos=1
                            )
                    else:
                        await mqtt_client.subscribe("diyhome/+/shadow/reported", qos=1)

                    async for msg in mqtt_client.messages:
                        if self._stopping:
                            break
                        topic_str = str(msg.topic)
                        parts = topic_str.split("/")
                        if len(parts) < 4 or parts[0] != "diyhome":
                            continue
                        dev_uid = parts[1]
                        msg_type = parts[2]
                        msg_sub  = parts[3] if len(parts) > 3 else ""

                        if msg_type == "shadow" and msg_sub == "reported":
                            try:
                                payload = json.loads(msg.payload)
                            except Exception:
                                continue
                            await self._handle_mqtt_shadow(dev_uid, payload)

            except Exception as err:
                self._mqtt_pub = None
                self.mqtt_connected = False
                if not self._stopping:
                    _LOGGER.warning(
                        "DiyHome MQTT: errore connessione (%s) — retry tra %ds",
                        err, retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 120)

        self._mqtt_pub = None
        self.mqtt_connected = False

    async def _handle_mqtt_shadow(self, uid: str, reported: dict) -> None:
        """Aggiorna dati coordinator da shadow/reported MQTT (real-time, zero cloud)."""
        if not self.data or uid not in self.data:
            return
        current = dict(self.data[uid])

        # Valve 1
        v1 = reported.get("valve1")
        if isinstance(v1, dict):
            current["valve1_state"] = v1.get("state", current.get("valve1_state"))
        elif isinstance(v1, str):
            current["valve1_state"] = v1

        # Valve 2
        v2 = reported.get("valve2")
        if isinstance(v2, dict):
            current["valve2_state"] = v2.get("state", current.get("valve2_state"))
        elif isinstance(v2, str):
            current["valve2_state"] = v2

        # Pompa
        pump = reported.get("pump")
        if isinstance(pump, dict):
            current["pump"] = {**(current.get("pump") or {}), **pump}
        elif isinstance(pump, bool):
            current["pump"] = {**(current.get("pump") or {}), "isOn": pump}

        # Sensori
        sensors = reported.get("sensors")
        if isinstance(sensors, dict):
            current["sensors"] = {**(current.get("sensors") or {}), **sensors}

        # Zone irrigazione
        zones = reported.get("zones")
        if isinstance(zones, dict):
            current["zones"] = {**(current.get("zones") or {}), **zones}

        new_data = {**self.data, uid: current}
        self._lan_last_update = time.monotonic()  # anti-stale cloud SSE
        self.async_set_updated_data(new_data)
        _LOGGER.debug("DiyHome MQTT: shadow/reported → %s aggiornato", uid)

    # ── Invio comandi ─────────────────────────────────────────────────────────

    async def async_send_command(
        self, uid: str, action: str, payload: dict | None = None
    ) -> bool:
        """Invia comando: LAN HTTP → MQTT locale → cloud (priorità decrescente).

        LAN HTTP è il canale primario perché:
        - topic MQTT locale non ancora allineati al firmware (valve1/set vs valve/set)
        - LAN HTTP usa lo stesso CommandDispatcher firmware di cloud e app mobile
        - zero ambiguità su payload e topic
        MQTT locale è mantenuto come secondario per scenari Mosquitto puri (senza LAN HTTP).
        """
        # 1. LAN diretta (HTTP) — canale primario, stessa latenza di MQTT ma affidabile
        if self.lan_mode and self.lan_client.is_available():
            # FIX C3: rinnova il JWT LAN se manca o scade entro 24h.
            # Senza questo, dopo 30gg il token scadeva → send_command restituiva
            # False silenziosamente → fallback cloud senza avvisare l'utente.
            if self.lan_client.needs_renewal():
                _LOGGER.debug("DiyHome: JWT LAN in scadenza/assente → rinnovo per uid=%s", uid)
                await self._fetch_lan_jwt(uid)
            try:
                ok = await self.lan_client.send_command(action, payload or {})
                if ok:
                    # FIX I6: GET di conferma asincrono con delay 350ms
                    # (il firmware esegue comandi async — GET immediato dava stato vecchio)
                    self.hass.async_create_task(
                        self._confirm_lan_after_command(uid),
                        name=f"diyhome_confirm_lan_{uid}",
                    )
                    return True
                _LOGGER.debug("DiyHome LAN command fallito, fallback cloud")
            except Exception as err:
                _LOGGER.debug("DiyHome LAN command error (%s), fallback cloud", err)

        # 2. MQTT locale — secondario (topic non ancora allineati al firmware)
        # valve1/set firmware non corrisponde a valve/set → usare solo se LAN HTTP non disponibile
        if self.mqtt_connected and self._mqtt_pub is not None:
            try:
                mqtt_topic, mqtt_payload = self._action_to_mqtt(uid, action, payload)
                if mqtt_topic:
                    import aiomqtt  # noqa: PLC0415
                    await self._mqtt_pub.publish(  # type: ignore[attr-defined]
                        mqtt_topic, json.dumps(mqtt_payload).encode(), qos=1
                    )
                    _LOGGER.debug("DiyHome MQTT: cmd %s → %s", action, mqtt_topic)
                    self.hass.async_create_task(
                        self._confirm_lan_after_command(uid),
                        name=f"diyhome_confirm_mqtt_{uid}",
                    )
                    return True
            except Exception as err:
                _LOGGER.debug("DiyHome MQTT cmd error (%s) — fallback cloud", err)

        # 3. Cloud fallback
        cmd_payload: dict = {"action": action}
        if payload:
            cmd_payload.update(payload)
        await self.client.send_command(uid, action, payload or {})
        return True


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

    # Auto-detect IP dai dati cloud se mdns_hostname non configurato manualmente
    # (funziona anche in HA Docker/container dove zeroconf non risolve .local)
    if not mdns_hostname and coordinator.data:
        cloud_ip = coordinator._extract_ip_from_cloud_data()
        if cloud_ip:
            _LOGGER.info(
                "DiyHome: IP auto-rilevato dai dati cloud → %s — tentativo LAN mode",
                cloud_ip,
            )
            mdns_hostname = cloud_ip
            lan_client.mdns_hostname = cloud_ip
            if coordinator._session is None or coordinator._session.closed:
                coordinator._session = aiohttp.ClientSession()
            lan_client.session = coordinator._session
            # Salva in entry.data per sopravvivere a riavvii HA
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_MDNS_HOSTNAME: cloud_ip},
            )
            # Prima fetch LAN con IP cloud
            try:
                states = await asyncio.wait_for(
                    lan_client.get_all_states(), timeout=LAN_CONNECT_TIMEOUT
                )
                if states:
                    coordinator.async_set_updated_data(states)
                    coordinator.lan_mode = True
                    _LOGGER.info(
                        "DiyHome: LAN mode attiva via IP cloud (%s)", cloud_ip
                    )
            except Exception:
                pass

    runtime_data = DiyHomeRuntimeData(coordinator=coordinator, client=client)
    entry.runtime_data = runtime_data

    # Migrazione v2.5.0: rimuovi entità obsolete dal registry HA.
    # HA non cancella automaticamente le entità eliminate dal componente —
    # restano come "Non disponibile" finché non vengono esplicitamente rimosse.
    _OBSOLETE_ENTITY_KEYS: list[tuple[str, str]] = [
        ("sensor", "cpu_temp"),
        ("sensor", "free_heap"),
        ("sensor", "daily_avg"),
        ("sensor", "valve1_type"),
        ("sensor", "valve2_type"),
    ]
    ent_reg = er.async_get(hass)
    for uid in list(coordinator.data or {}):
        for platform, key in _OBSOLETE_ENTITY_KEYS:
            unique_id = f"{uid}_{key}"
            entity_id = ent_reg.async_get_entity_id(platform, "diyhome", unique_id)
            if entity_id:
                ent_reg.async_remove(entity_id)
                _LOGGER.info("DiyHome [migrazione v2.5.0]: rimossa entità obsoleta %s (%s)", entity_id, unique_id)

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
