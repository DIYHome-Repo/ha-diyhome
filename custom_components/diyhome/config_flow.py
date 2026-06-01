"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    LocalOAuth2Implementation,
)

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, OAUTH2_TOKEN

_LOGGER = logging.getLogger(__name__)

# Relay Nabu Casa — Universal Link registrato dall'app HA iOS.
# Quando SFSafariViewController naviga su questo dominio, iOS intercetta
# tramite Universal Links e passa il codice OAuth direttamente all'app HA
# senza che il browser debba raggiungere l'URL locale di HA.
# Funziona sia per istanze HTTP che HTTPS, sia in LAN che in remoto.
_MY_REDIRECT = "https://my.home-assistant.io/redirect/oauth"


class DiyHomeLocalOAuth2Implementation(LocalOAuth2Implementation):
    """Forza sempre il relay my.home-assistant.io come redirect_uri.

    Senza questo override, istanze HTTP usano l'URL locale HA
    (es. http://homeassistant.local:8123/auth/external/callback).
    Quell'URL non è raggiungibile da SFSafariViewController su iOS
    (mDNS .local non funziona nel sandbox del browser), causando la
    pagina di errore 'data:' di Safari e poi 'Sei disconnesso' nell'app.

    Con my.home-assistant.io, l'app iOS HA intercetta via Universal Links
    e gestisce il callback direttamente — il browser non deve mai
    raggiungere l'URL locale di HA.
    """

    @property
    def redirect_uri(self) -> str:
        """Usa sempre il relay my.home-assistant.io (Universal Links iOS)."""
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
