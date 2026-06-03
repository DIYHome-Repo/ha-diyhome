"""DiyHome integration for Home Assistant — v2.0.0 con MQTT locale + fallback REST/SSE."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

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
MQTT_CONNECT_TIMEOUT = 10  # secondi


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
        on_message_cb,  # coroutine (topic: str, payload: bytes) -> None
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._uids = list(uids)
        self._on_message_cb = on_message_cb
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
        self._stop_event.set()
        if self._client:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass

    def publish(self, topic: str, payload: str | bytes) -> None:
        """Pubblica un comando MQTT (thread-safe)."""
        if self._client and self._connected:
            try:
                self._client.publish(topic, payload, qos=1)
            except Exception as err:
                _LOGGER.debug("DiyHome MQTT publish error: %s", err)

    def wait_connected(self, timeout: float = MQTT_CONNECT_TIMEOUT) -> bool:
        return self._connected_event.wait(timeout=timeout)

    # ── Thread interno ────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            _LOGGER.error("DiyHome: paho-mqtt non disponibile — MQTT locale disabilitato")
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
                # Aspetta disconnect o stop
                while not self._stop_event.is_set():
                    time.sleep(1)
                client.loop_stop()
                return
            except Exception as err:
                _LOGGER.debug("DiyHome MQTT connect error (%s), retry in 30s", err)
                self._connected = False
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
                _LOGGER.debug("DiyHome MQTT: subscribed diyhome/%s/#", uid)
        else:
            _LOGGER.warning("DiyHome MQTT locale: connect failed rc=%s", rc)
            self._connected_event.set()

    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            _LOGGER.debug("DiyHome MQTT locale: disconnesso (rc=%s)", rc)

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
        )

        self._mqtt.start()

        # Aspetta connessione con timeout
        connected = await self.hass.async_add_executor_job(
            self._mqtt.wait_connected, MQTT_CONNECT_TIMEOUT
        )

        if connected and self._mqtt.is_connected():
            self.mqtt_mode = True
            _LOGGER.info(
                "DiyHome: modalità MQTT locale attiva (%s device)",
                len(uids),
            )
            return True
        else:
            _LOGGER.warning(
                "DiyHome: MQTT locale non raggiungibile entro %ss — modalità cloud",
                MQTT_CONNECT_TIMEOUT,
            )
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

    # ── Handler messaggi MQTT ─────────────────────────────────────────────────

    async def _handle_mqtt_message(self, topic: str, raw: bytes) -> None:
        """Processa un messaggio MQTT locale e aggiorna coordinator.data."""
        try:
            parts = topic.split("/")
            # Formato: diyhome/{uid}/{type}[/{subtype}]
            if len(parts) < 3 or parts[0] != "diyhome":
                return

            uid = parts[1]
            if not self.data or uid not in self.data:
                return

            msg_type = parts[2] if len(parts) > 2 else ""
            subtype = parts[3] if len(parts) > 3 else ""

            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = raw.decode("utf-8", errors="replace").strip()

            new_data = dict(self.data)
            device = dict(new_data.get(uid, {}))

            updated = False

            if msg_type == "availability":
                online = (payload == "online") if isinstance(payload, str) else bool(payload)
                if device.get("online") != online:
                    device["online"] = online
                    updated = True

            elif msg_type == "valve" and subtype == "state":
                if isinstance(payload, dict):
                    valve = dict(device.get("valve1") or {})
                    is_open = payload.get("is_open", payload.get("open", False))
                    if valve.get("is_open") != is_open:
                        valve["is_open"] = is_open
                        device["valve1"] = valve
                        updated = True

            elif msg_type == "valve2" and subtype == "state":
                if isinstance(payload, dict):
                    valve = dict(device.get("valve2") or {})
                    is_open = payload.get("is_open", payload.get("open", False))
                    if valve.get("is_open") != is_open:
                        valve["is_open"] = is_open
                        device["valve2"] = valve
                        updated = True

            elif msg_type == "telemetry":
                if subtype == "tank" and isinstance(payload, dict):
                    tel = dict(device.get("telemetry") or {})
                    tel.update({
                        k: payload[k]
                        for k in ("percentage", "liters", "ambientTemp")
                        if k in payload
                    })
                    device["telemetry"] = tel
                    updated = True

                elif subtype == "flow" and isinstance(payload, dict):
                    device["flow"] = {
                        "in": payload.get("in", payload.get("flowIn")),
                        "out": payload.get("out", payload.get("flowOut")),
                    }
                    updated = True

                elif subtype == "heartbeat":
                    device["online"] = True
                    updated = True

            elif msg_type == "irrigation" and subtype == "state":
                if isinstance(payload, list):
                    device["zones"] = payload
                    updated = True
                elif isinstance(payload, dict) and "zones" in payload:
                    device["zones"] = payload["zones"]
                    updated = True

            elif msg_type == "pump":
                if isinstance(payload, dict):
                    device["pump"] = payload
                    updated = True

            if updated:
                new_data[uid] = device
                self.async_set_updated_data(new_data)

        except Exception as err:
            _LOGGER.debug("DiyHome MQTT message parse error (%s): %s", topic, err)

    # ── REST polling (safety-net) ──────────────────────────────────────────────

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


# Alias per compatibilità con switch.py/sensor.py/binary_sensor.py esistenti
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

    # Avvia SSE solo se MQTT locale NON è attivo (evita connessioni doppie)
    if not mqtt_active:
        sse_task = hass.async_create_task(
            _listen_sse(hass, entry, coordinator),
            name=f"diyhome_sse_{entry.entry_id}",
        )
        runtime_data.sse_task = sse_task
    else:
        _LOGGER.info(
            "DiyHome: SSE disabilitato (MQTT locale attivo)"
        )

    # Ricarica al cambio opzioni (es. broker MQTT)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica l'integrazione quando le opzioni cambiano."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data: DiyHomeRuntimeData = entry.runtime_data

    sse_task = runtime_data.sse_task
    if sse_task and not sse_task.done():
        sse_task.cancel()
        try:
            await sse_task
        except (asyncio.CancelledError, Exception):
            pass

    runtime_data.coordinator.stop_mqtt()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ─────────────────────────────────────────────────────────────────────────────
# SSE listener (fallback quando MQTT locale non è attivo)
# ─────────────────────────────────────────────────────────────────────────────

async def _listen_sse(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DualModeCoordinator,
) -> None:
    """Long-running task SSE: aggiornamento real-time — fallback quando MQTT locale non disponibile."""
    import aiohttp

    stream_url = f"{CLOUD_URL}/api/ha/stream"
    token = entry.data.get("access_token", "")
    headers = {"Authorization": f"Bearer {token}"}
    _LOGGER.debug("DiyHome SSE: avvio listener %s", stream_url)

    while True:
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
