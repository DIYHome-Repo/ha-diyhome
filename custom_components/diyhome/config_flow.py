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

        self.flow_impl = LocalOAuth2Implementation(
            self.hass,
            DOMAIN,
            OAUTH2_CLIENT_ID,
            OAUTH2_CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        )
        return await self.async_step_auth()
