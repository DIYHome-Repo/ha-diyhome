"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    LocalOAuth2Implementation,
)

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, OAUTH2_TOKEN

_LOGGER = logging.getLogger(__name__)

# Relay Nabu Casa — fallback per istanze senza external_url HTTPS.
_MY_REDIRECT = "https://my.home-assistant.io/redirect/oauth"

# Path callback standard di HA per OAuth2 esterno.
_HA_CALLBACK_PATH = "/auth/external/callback"


def _is_local_address(url: str) -> bool:
    """Restituisce True se l'URL punta a un indirizzo locale/mDNS."""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        return True
    return (
        hostname.endswith(".local")
        or hostname in ("localhost", "127.0.0.1", "::1")
        or hostname.startswith("192.168.")
        or hostname.startswith("10.")
        or (hostname.startswith("172.") and any(
            hostname.startswith(f"172.{i}.") for i in range(16, 32)
        ))
    )


class DiyHomeLocalOAuth2Implementation(LocalOAuth2Implementation):
    """Sceglie il redirect_uri ottimale in base alla configurazione HA.

    Strategia:
    - Se HA ha una external_url HTTPS (DuckDNS, Nabu Casa, dominio custom):
        usa {external_url}/auth/external/callback direttamente.
        Il browser (desktop o mobile) raggiunge sempre questo URL senza
        dipendere da localStorage di my.home-assistant.io.
    - Altrimenti (solo URL locale / mDNS .local / IP privato):
        usa my.home-assistant.io come relay.
        Evita il problema mDNS in SFSafariViewController su iOS
        (http://homeassistant.local:8123 non risolve nel sandbox Safari).
    """

    @property
    def redirect_uri(self) -> str:
        """Seleziona il redirect_uri in base al tipo di URL HA."""
        external_url: str | None = getattr(self.hass.config, "external_url", None)

        if external_url and external_url.startswith("https://") and not _is_local_address(external_url):
            clean = external_url.rstrip("/")
            _LOGGER.debug("DiyHome OAuth: redirect diretto a %s%s", clean, _HA_CALLBACK_PATH)
            return f"{clean}{_HA_CALLBACK_PATH}"

        _LOGGER.debug("DiyHome OAuth: nessuna external_url HTTPS, uso relay my.home-assistant.io")
        return _MY_REDIRECT


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Config flow DiyHome con OAuth2 tramite relay my.home-assistant.io."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Avvia il flow OAuth2."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        self.flow_impl = DiyHomeLocalOAuth2Implementation(
            self.hass,
            DOMAIN,
            OAUTH2_CLIENT_ID,
            OAUTH2_CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        )
        return await self.async_step_auth()

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Sovrascrive il titolo entry da 'Local application credentials' a 'DiyHome Cloud'."""
        return self.async_create_entry(title="DiyHome Cloud", data=data)
