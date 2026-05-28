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


class DiyHomeLocalOAuth2Implementation(LocalOAuth2Implementation):
    """Override redirect_uri per bypassare my.home-assistant.io.

    LocalOAuth2Implementation usa https://my.home-assistant.io/redirect/oauth
    quando l'istanza HA ha base_url HTTPS — relay che richiede abbonamento
    Nabu Casa. Con DuckDNS o altri certificati custom senza Nabu Casa,
    questo causa un errore 520.

    Questa sottoclasse forza il redirect direttamente sull'URL locale HA
    (es. https://qualcosa.duckdns.org:8123/auth/external/callback),
    eliminando la dipendenza dal relay Nabu Casa.
    """

    @property
    def redirect_uri(self) -> str:
        """Usa l'URL base HA direttamente, bypassando my.home-assistant.io."""
        try:
            base_url = self.hass.config.api.base_url
            if base_url:
                return f"{base_url.rstrip('/')}/auth/external/callback"
        except (AttributeError, TypeError):
            pass
        return super().redirect_uri


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Config flow DiyHome.

    Imposta self.flow_impl direttamente con le credenziali fisse DiyHome,
    bypassa async_step_pick_implementation (evita dialog 'Aggiungi credenziali')
    e salta al redirect OAuth2. Funziona sia alla prima installazione che ai
    restart successivi — non richiede che async_setup() sia già stato chiamato.
    """

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
