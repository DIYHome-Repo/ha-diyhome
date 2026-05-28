"""DiyHome integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from aiohttp import ClientResponseError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DiyHomeApiClient
from .const import (
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_CLIENT_ID,
    OAUTH2_CLIENT_SECRET,
    OAUTH2_TOKEN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


def _oauth_implementation(
    hass: HomeAssistant,
) -> config_entry_oauth2_flow.LocalOAuth2Implementation:
    """Crea l'implementazione OAuth2 DiyHome direttamente.

    Costruisce LocalOAuth2Implementation senza passare per il registry globale.
    Questo evita il 500 'Server got itself in trouble' durante la prima installazione
    (quando async_setup non è ancora stato chiamato da HA) e funziona correttamente
    anche ai restart successivi.
    """
    return config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass,
        DOMAIN,
        OAUTH2_CLIENT_ID,
        OAUTH2_CLIENT_SECRET,
        OAUTH2_AUTHORIZE,
        OAUTH2_TOKEN,
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up DiyHome."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DiyHome from a config entry."""
    implementation = _oauth_implementation(hass)
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    client = DiyHomeApiClient(session)

    coordinator = DiyHomeCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class DiyHomeCoordinator(DataUpdateCoordinator):
    """Coordinator che aggiorna i dati device ogni SCAN_INTERVAL."""

    def __init__(self, hass: HomeAssistant, client: DiyHomeApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.get_devices()
            devices = data.get("devices", [])
            return {d["uid"]: d for d in devices if d.get("uid")}
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise ConfigEntryAuthFailed(
                    "DiyHome OAuth token non valido o scaduto"
                ) from err
            raise UpdateFailed(
                f"DiyHome API HTTP error {err.status}: {err.message}"
            ) from err
        except Exception as err:
            raise UpdateFailed(f"DiyHome API error: {err}") from err
