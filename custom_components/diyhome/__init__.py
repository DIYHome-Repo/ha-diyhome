"""DiyHome integration for Home Assistant — v2.0.0 con MQTT locale + fallback REST/SSE."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiyHomeApiClient
from .const import (
    CLOUD_URL,
    CONF_MQTT_ENABLED,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_USERNAME,
    DEFAULT_MQTT_PORT,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)
MQTT_CONNECT_TIMEOUT = 10       # secondi attesa connessione iniziale
MQTT_OFFLINE_THRESHOLD = 45     # secondi di disconnessione prima di avviare SSE fallback


# ─────────────────────────────────────────────────────────────────────────────
# Normalizzatori payload MQTT → struttura dati attesa da sensor.py / switch.py
# ─────────────────────────────────────────────────────────────────────────────

def _norm_tank(payload: dict) -> dict:
    """Normalizza payload tank MQTT → device['tank'] atteso da sensor.py."""
    return {
        "level_pct": (
            payload.get("level_pct")
            or payload.get("percentage")
            or payload.get("level")
        ),
        "liters": payload.get("liters") or payload.get("volume"),
        "temperature": (
            payload.get("temperature")
            or payload.get("temp")
            or payload.get("ambientTemp")
        ),
    }


def _norm_flow(payload: dict) -> dict:
    """Normalizza payload flow MQTT → device['flow'] atteso da sensor.py."""
    return {
        "flow_in_rate": (
            payload.get("flow_in_rate")
            or payload.get("in")
            or payload.get("flowIn")
            or payload.get("flow_in")
        ),
        "flow_out_rate": (
            payload.get("flow_out_rate")
            or payload.get("out")
            or payload.get("flowOut")
            or payload.get("flow_out")
        ),
    }


def _norm_diagnostics(existing: dict, payload: dict) -> dict:
    """Fonde payload heartbeat nei diagnostics → device['diagnostics']."""
    diag = dict(existing or {})
    wifi = payload.get("wifi", {}) if isinstance(payload.get("wifi"), dict) else {}
    diag.update({
        k: v for k, v in {
            "rssi": payload.get("rssi") or wifi.get("rssi"),
            "uptime": payload.get("uptime"),
            "ssid": payload.get("ssid") or wifi.get("ssid"),
            "ip_address": payload.get("ip") or payload.get("ip_address") or wifi.get("ip"),
        }.items() if v is not None
    })
    return diag


# ─────────────────────────────────────────────────────────────────────────────
# MQTT Local Client (paho-mqtt in thread separato)
# ─────────────────────────────────────────────────────────────────────────────

class DiyHomeMqttLocal:
    """Client paho-mqtt per connessione diretta al broker Mosquitto locale."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        uids: list[str],
        on_message_cb,   # coroutine (topic: str, payload: bytes) -> None
        on_connect_cb,   # callable () -> None  [thread-safe, called when connected]
        on_disconnect_cb, # callable () -> None [thread-safe, called on disconnect]
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._uids = list(uids)
        self._on_message_cb = on_message_cb
        self._on_connect_cb = on_connect_cb
        self._on_disconnect_cb = on_disconnect_cb
        self._client = None
        self._connected_event = threading.Event()
        self._connected = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="diyhome_mqtt_local"
        )
        self._thread.start()

    def stop(self) -> None:
        """Ferma il thread in modo sicuro — chiamabile più volte."""
        self._stop_event.set()
        self._connected = False
        if self._client:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass
        self._client = None

    def publish(self, topic: str, payload: str | bytes) -> None:
        """Pubblica un comando MQTT (thread-safe)."""
        if self._client and self._connected:
            try:
                self._client.publish(topic, payload, qos=1)
            except Exception as err:
                _LOGGER.debug("DiyHome MQTT publish error: %s", err)

    def wait_connected(self, timeout: float = MQTT_CONNECT_TIMEOUT) -> bool:
        """Blocca fino alla connessione (o timeout). Usare in executor."""
        return self._connected_event.wait(timeout=timeout)

    # ── Thread interno ────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            _LOGGER.error("DiyHome: paho-mqtt non disponibile — MQTT locale disabilitato")
            self._connected_event.set()  # sblocca wait_connected
            return

        client = mqtt.Client(
            client_id=f"diyhome-ha-{int(time.time())}",
            clean_session=True,
        )
        self._client = client

        if self._username:
            client.username_pw_set(self._username, self._password or "")
        if self._use_tls:
            client.tls_set()

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        while not self._stop_event.is_set():
            try:
                client.connect(self._host, self._port, keepalive=60)
                client.loop_start()
                while not self._stop_event.is_set():
                    time.sleep(1)
                client.loop_stop()
                return
            except Exception as err:
                _LOGGER.debug("DiyHome MQTT connect error (%s), retry in 30s", err)
                self._connected = False
                self._connected_event.set()  # sblocca wait con _connected=False
                for _ in range(30):
                    if self._stop_event.is_set():
                        return
                    time.sleep(1)

    def _on_connect(self, client, userdata, flags, rc) -> None:
        if rc == 0:
            self._connected = True
            self._connected_event.set()
            _LOGGER.info(
                "DiyHome MQTT locale: connesso a %s:%s", self._host, self._port
            )
            for uid in self._uids:
                client.subscribe(f"diyhome/{uid}/#", qos=0)
            if self._on_connect_cb:
                try:
                    self._on_connect_cb()
                except Exception:
                    pass
        else:
            _LOGGER.warning("DiyHome MQTT locale: connect failed rc=%s", rc)
            self._connected_event.set()

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            _LOGGER.debug("DiyHome MQTT locale: disconnesso (rc=%s)", rc)
        if self._on_disconnect_cb:
            try:
                self._on_disconnect_cb()
            except Exception:
                pass

    def _on_message(self, client, userdata, msg) -> None:
        """Callback paho — chiama la coroutine nel loop HA (thread-safe)."""
        try:
            asyncio.run_coroutine_threadsafe(
                self._on_message_cb(msg.topic, msg.payload),
                self._hass.loop,
            )
        except Exception as err:
            _LOGGER.debug("DiyHome MQTT message dispatch error: %s", err)


# ─────────────────────────────────────────────────────────────────────────────
# DualModeCoordinator
# ─────────────────────────────────────────────────────────────────────────────

class DualModeCoordinator(DataUpdateCoordinator):
    """Coordinator duale: MQTT locale (primary) + REST/SSE (fallback/safety-net)."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: DiyHomeApiClient,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._entry = entry
        self._online_states: dict[str, bool] = {}
        self._whoami_logged = False
        self._mqtt: DiyHomeMqttLocal | None = None
        self.mqtt_mode = False

        # SSE runtime fallback
        self._sse_task: asyncio.Task | None = None
        self._mqtt_disconnect_at: float | None = None

    # ── Setup MQTT locale ─────────────────────────────────────────────────────

    async def async_setup_mqtt(self) -> bool:
        """Avvia client MQTT locale se configurato. Ritorna True se connesso."""
        opts = self._entry.options
        if not opts.get(CONF_MQTT_ENABLED) or not opts.get(CONF_MQTT_HOST):
            return False

        uids = list(self.data.keys()) if self.data else []
        if not uids:
            _LOGGER.debug("DiyHome MQTT: nessun device trovato, MQTT locale non avviato")
            return False

        self._mqtt = DiyHomeMqttLocal(
            hass=self.hass,
            host=opts[CONF_MQTT_HOST],
            port=int(opts.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
            username=opts.get(CONF_MQTT_USERNAME, ""),
            password=opts.get(CONF_MQTT_PASSWORD, ""),
            use_tls=opts.get(CONF_MQTT_TLS, False),
            uids=uids,
            on_message_cb=self._handle_mqtt_message,
            on_connect_cb=self._on_mqtt_connect,
            on_disconnect_cb=self._on_mqtt_disconnect,
        )

        self._mqtt.start()

        # Aspetta connessione con timeout
        connected = await self.hass.async_add_executor_job(
            self._mqtt.wait_connected, MQTT_CONNECT_TIMEOUT
        )

        if connected and self._mqtt.is_connected():
            self.mqtt_mode = True
            _LOGGER.info(
                "DiyHome: modalità MQTT locale attiva (%s device)", len(uids)
            )
            return True
        else:
            # Timeout: ferma il thread (fix lifecycle leak) e torna al cloud
            _LOGGER.warning(
                "DiyHome: MQTT locale non raggiungibile entro %ss — modalità cloud",
                MQTT_CONNECT_TIMEOUT,
            )
            self._mqtt.stop()
            self._mqtt = None
            return False

    def stop_mqtt(self) -> None:
        if self._mqtt:
            self._mqtt.stop()
            self._mqtt = None
        self.mqtt_mode = False

    def mqtt_publish(self, topic: str, payload: str) -> bool:
        """Pubblica su MQTT locale. Ritorna True se OK."""
        if self._mqtt and self._mqtt.is_connected():
            self._mqtt.publish(topic, payload)
            return True
        return False

    # ── Gestione runtime fallback SSE ─────────────────────────────────────────

    def _on_mqtt_connect(self) -> None:
        """Chiamato dal thread MQTT quando (ri)connesso — ferma SSE se attivo."""
        self.mqtt_mode = True
        self._mqtt_disconnect_at = None
        if self._sse_task and not self._sse_task.done():
            _LOGGER.info("DiyHome: MQTT locale riconnesso — fermo SSE fallback")
            self._sse_task.cancel()
            self._sse_task = None

    def _on_mqtt_disconnect(self) -> None:
        """Chiamato dal thread MQTT quando disconnesso — schedula SSE fallback."""
        self.mqtt_mode = False
        self._mqtt_disconnect_at = time.monotonic()
        # Schedula il check nel loop HA (thread-safe)
        try:
            self.hass.loop.call_soon_threadsafe(self._schedule_sse_fallback)
        except Exception:
            pass

    def _schedule_sse_fallback(self) -> None:
        """Avvia il task SSE fallback se non già attivo."""
        if self._sse_task and not self._sse_task.done():
            return  # SSE già attivo
        _LOGGER.info(
            "DiyHome: MQTT locale disconnesso — avvio SSE fallback"
        )
        self._sse_task = self.hass.async_create_task(
            _listen_sse(self.hass, self._entry, self),
            name=f"diyhome_sse_fallback_{self._entry.entry_id}",
        )

    def set_sse_task(self, task: asyncio.Task | None) -> None:
        """Registra il task SSE esterno (quando MQTT non è attivo allo startup)."""
        self._sse_task = task

    def cancel_sse_task(self) -> None:
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            self._sse_task = None

    # ── Handler messaggi MQTT ─────────────────────────────────────────────────

    async def _handle_mqtt_message(self, topic: str, raw: bytes) -> None:
        """Processa un messaggio MQTT locale e aggiorna coordinator.data.

        Struttura dati normalizzata per compatibilità con sensor.py / switch.py:
          device["tank"]   = {level_pct, liters, temperature}
          device["flow"]   = {flow_in_rate, flow_out_rate}
          device["valve1"] = {is_open, name}
          device["valve2"] = {is_open}
          device["zones"]  = [{index, is_active, ...}]
          device["pump"]   = {mode, relay_on, is_locked}
          device["diagnostics"] = {rssi, uptime, ssid, ip_address}
          device["online"] = bool
        """
        try:
            parts = topic.split("/")
            # Formato: diyhome/{uid}/{type}[/{subtype}]
            if len(parts) < 3 or parts[0] != "diyhome":
                return

            uid = parts[1]
            if not self.data or uid not in self.data:
                return

            msg_type = parts[2] if len(parts) > 2 else ""
            subtype   = parts[3] if len(parts) > 3 else ""

            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = raw.decode("utf-8", errors="replace").strip()

            new_data = dict(self.data)
            device   = dict(new_data.get(uid, {}))
            updated  = False

            # ── availability ──────────────────────────────────────────────────
            if msg_type == "availability":
                online = (payload == "online") if isinstance(payload, str) else bool(payload)
                if device.get("online") != online:
                    device["online"] = online
                    updated = True

            # ── valve/state ───────────────────────────────────────────────────
            elif msg_type == "valve" and subtype == "state" and isinstance(payload, dict):
                valve = dict(device.get("valve1") or {})
                is_open = payload.get("is_open", payload.get("open", False))
                if valve.get("is_open") != is_open:
                    valve["is_open"] = is_open
                    device["valve1"] = valve
                    updated = True

            # ── valve2/state ──────────────────────────────────────────────────
            elif msg_type == "valve2" and subtype == "state" and isinstance(payload, dict):
                valve = dict(device.get("valve2") or {})
                is_open = payload.get("is_open", payload.get("open", False))
                if valve.get("is_open") != is_open:
                    valve["is_open"] = is_open
                    device["valve2"] = valve
                    updated = True

            # ── telemetry/* ───────────────────────────────────────────────────
            elif msg_type == "telemetry":
                if subtype == "tank" and isinstance(payload, dict):
                    # Normalizza nella struttura attesa da sensor.py: device["tank"]
                    device["tank"] = _norm_tank(payload)
                    updated = True

                elif subtype == "flow" and isinstance(payload, dict):
                    # Normalizza nella struttura attesa da sensor.py: device["flow"]
                    device["flow"] = _norm_flow(payload)
                    updated = True

                elif subtype == "heartbeat" and isinstance(payload, dict):
                    # Heartbeat → mark online + aggiorna diagnostics
                    device["online"] = True
                    device["diagnostics"] = _norm_diagnostics(
                        device.get("diagnostics"), payload
                    )
                    if "firmware" in payload:
                        device["firmware"] = payload["firmware"]
                    # Consumo giornaliero se incluso nell'heartbeat
                    if "liters_in" in payload or "liters_out" in payload:
                        cons = dict(device.get("consumption_today") or {})
                        if "liters_in" in payload:
                            cons["liters_in"] = payload["liters_in"]
                        if "liters_out" in payload:
                            cons["liters_out"] = payload["liters_out"]
                        device["consumption_today"] = cons
                    updated = True

            # ── irrigation/state ──────────────────────────────────────────────
            elif msg_type == "irrigation" and subtype == "state":
                if isinstance(payload, list):
                    device["zones"] = payload
                    updated = True
                elif isinstance(payload, dict) and "zones" in payload:
                    device["zones"] = payload["zones"]
                    updated = True

            # ── pump ─────────────────────────────────────────────────────────
            elif msg_type == "pump" and not subtype and isinstance(payload, dict):
                device["pump"] = payload
                updated = True

            if updated:
                new_data[uid] = device
                self.async_set_updated_data(new_data)

        except Exception as err:
            _LOGGER.debug("DiyHome MQTT message parse error (%s): %s", topic, err)

    # ── REST polling (safety-net / fallback) ──────────────────────────────────

    async def _async_update_data(self) -> dict:
        try:
            if not self._whoami_logged:
                try:
                    whoami = await self.client.whoami()
                    _LOGGER.debug(
                        "DiyHome WHOAMI → userId=%s email=%s allDevices=%s",
                        whoami.get("userId"),
                        whoami.get("email"),
                        [
                            f"{d.get('name')}(uid={d.get('device_uid')},ok={d.get('visibileInHA')})"
                            for d in whoami.get("allDevices", [])
                        ],
                    )
                except Exception as we:
                    _LOGGER.debug("DiyHome WHOAMI errore: %s", we)
                self._whoami_logged = True

            data = await self.client.get_devices()
            devices: dict[str, dict] = {
                d["uid"]: d for d in data.get("devices", []) if d.get("uid")
            }

            for uid, device in devices.items():
                online = device.get("online", False)
                was_online = self._online_states.get(uid)
                if was_online is True and not online:
                    _LOGGER.warning(
                        "DiyHome device %s (%s) è andato offline",
                        uid,
                        device.get("name", uid),
                    )
                elif was_online is False and online:
                    _LOGGER.info(
                        "DiyHome device %s (%s) è tornato online",
                        uid,
                        device.get("name", uid),
                    )
            self._online_states = {uid: d.get("online", False) for uid, d in devices.items()}

            return devices

        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"DiyHome API error: {err}") from err


# Alias per compatibilità con switch.py / sensor.py / binary_sensor.py
DiyHomeCoordinator = DualModeCoordinator


# ─────────────────────────────────────────────────────────────────────────────
# Runtime data
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiyHomeRuntimeData:
    """Dati runtime associati alla config entry DiyHome."""

    coordinator: DualModeCoordinator
    client: DiyHomeApiClient
    sse_task: asyncio.Task | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Setup / Unload
# ─────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DiyHome from a config entry."""
    client = DiyHomeApiClient(hass, entry)
    coordinator = DualModeCoordinator(hass, client, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise ConfigEntryNotReady(f"DiyHome non raggiungibile: {err}") from err

    runtime_data = DiyHomeRuntimeData(coordinator=coordinator, client=client)
    entry.runtime_data = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Avvia MQTT locale se configurato
    mqtt_active = await coordinator.async_setup_mqtt()

    if not mqtt_active:
        # Nessun MQTT locale: SSE come sorgente push primaria
        sse_task = hass.async_create_task(
            _listen_sse(hass, entry, coordinator),
            name=f"diyhome_sse_{entry.entry_id}",
        )
        runtime_data.sse_task = sse_task
        coordinator.set_sse_task(sse_task)
    else:
        _LOGGER.info("DiyHome: SSE non avviato — MQTT locale attivo (si avvierà su disconnect)")

    # Ricarica al cambio opzioni (es. broker MQTT)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica l'integrazione quando le opzioni cambiano."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data: DiyHomeRuntimeData = entry.runtime_data

    # Cancella SSE
    runtime_data.coordinator.cancel_sse_task()
    if runtime_data.sse_task and not runtime_data.sse_task.done():
        runtime_data.sse_task.cancel()
        try:
            await runtime_data.sse_task
        except (asyncio.CancelledError, Exception):
            pass

    # Ferma MQTT locale
    runtime_data.coordinator.stop_mqtt()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ─────────────────────────────────────────────────────────────────────────────
# SSE listener (fallback quando MQTT locale non è attivo / si disconnette)
# ─────────────────────────────────────────────────────────────────────────────

async def _listen_sse(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DualModeCoordinator,
) -> None:
    """Long-running task SSE — fallback quando MQTT locale non disponibile."""
    import aiohttp

    stream_url = f"{CLOUD_URL}/api/ha/stream"
    token = entry.data.get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"}
    _LOGGER.debug("DiyHome SSE: avvio listener %s", stream_url)

    while True:
        # Se MQTT locale è tornato attivo, esci — il coordinator fermerà il task
        if coordinator.mqtt_mode:
            _LOGGER.debug("DiyHome SSE: MQTT locale attivo, SSE termina")
            return

        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.get(
                    stream_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, connect=15),
                ) as resp:
                    _LOGGER.debug("DiyHome SSE: connesso (HTTP %s)", resp.status)

                    if resp.status in (401, 403):
                        _LOGGER.warning("DiyHome SSE: token non valido, SSE disabilitato")
                        return

                    current_event: str | None = None

                    async for raw_line in resp.content:
                        # Se MQTT locale è tornato, interrompi SSE
                        if coordinator.mqtt_mode:
                            _LOGGER.debug("DiyHome SSE: MQTT riattivo, esco dal loop SSE")
                            return

                        line = raw_line.decode("utf-8").strip()

                        if not line or line.startswith(":"):
                            current_event = None
                            continue

                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                            continue

                        if line.startswith("data:") and current_event == "device_update":
                            try:
                                payload = json.loads(line[5:].strip())
                                uid = payload.get("uid")
                                if uid:
                                    embedded_state = payload.get("state")
                                    if embedded_state and uid in coordinator.data:
                                        new_data = dict(coordinator.data)
                                        new_data[uid] = embedded_state
                                        coordinator.async_set_updated_data(new_data)
                                    elif uid in coordinator.data:
                                        try:
                                            state = await coordinator.client.get_device_state(uid)
                                            new_data = dict(coordinator.data)
                                            new_data[uid] = state
                                            coordinator.async_set_updated_data(new_data)
                                        except ConfigEntryAuthFailed:
                                            return
                                        except Exception as state_err:
                                            _LOGGER.debug(
                                                "DiyHome SSE: fallback refresh (%s)",
                                                state_err,
                                            )
                                            await coordinator.async_request_refresh()
                                    elif embedded_state:
                                        await coordinator.async_request_refresh()
                            except (json.JSONDecodeError, Exception):
                                pass
                            current_event = None

        except asyncio.CancelledError:
            _LOGGER.debug("DiyHome SSE: task cancellato")
            return
        except Exception as err:
            _LOGGER.debug("DiyHome SSE: errore connessione (%s), retry in 5s", err)
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
