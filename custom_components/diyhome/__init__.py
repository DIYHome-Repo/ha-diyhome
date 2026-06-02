"""DiyHome integration for Home Assistant."""
from __future__ import annotations

import asyncio
import aiohttp
from dataclasses import dataclass
import json
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiyHomeApiClient, CLOUD_CALLBACK_URI
from .const import (
    CLOUD_URL,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


@dataclass
class DiyHomeRuntimeData:
    """Dati runtime associati alla config entry DiyHome."""

    coordinator: DiyHomeCoordinator
    client: DiyHomeApiClient
    sse_task: asyncio.Task | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DiyHome from a config entry."""
    client = DiyHomeApiClient(hass, entry)
    coordinator = DiyHomeCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    runtime_data = DiyHomeRuntimeData(coordinator=coordinator, client=client)
    entry.runtime_data = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    sse_task = hass.async_create_task(
        _listen_sse(hass, entry, coordinator),
        name=f"diyhome_sse_{entry.entry_id}",
    )
    runtime_data.sse_task = sse_task

    return True


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

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _listen_sse(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: "DiyHomeCoordinator",
) -> None:
    """Long-running task SSE: riceve push real-time dal backend DiyHome."""
    stream_url = f"{CLOUD_URL}/api/ha/stream"
    _LOGGER.debug("DiyHome SSE: avvio listener %s", stream_url)

    _last_refresh: dict[str, float] = {}
    _MIN_REFRESH_INTERVAL = 0.3

    while True:
        try:
            access_token = entry.data["token"]["access_token"]
            headers = {"Authorization": f"Bearer {access_token}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    stream_url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=None, connect=15),
                ) as resp:
                    _LOGGER.debug("DiyHome SSE: connesso (HTTP %s)", resp.status)

                    if resp.status in (401, 403):
                        _LOGGER.warning("DiyHome SSE: token non valido, retry in 60s")
                        await asyncio.sleep(60)
                        continue

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
                                    now = time.monotonic()
                                    last = _last_refresh.get(uid, 0.0)
                                    if now - last >= _MIN_REFRESH_INTERVAL:
                                        _last_refresh[uid] = now
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


class DiyHomeCoordinator(DataUpdateCoordinator):
    """Coordinator che aggiorna i dati device ogni SCAN_INTERVAL (fallback SSE)."""

    def __init__(self, hass: HomeAssistant, client: DiyHomeApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._online_states: dict[str, bool] = {}
        self._whoami_logged = False

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
                            f"{d.get('name')}(uid={d.get('device_uid')},claimed={d.get('claimed_at')},ok={d.get('visibileInHA')})"
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
            uids = list(devices.keys())
            _LOGGER.debug(
                "DiyHome API returned %d device(s): %s",
                len(devices),
                uids,
            )

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
