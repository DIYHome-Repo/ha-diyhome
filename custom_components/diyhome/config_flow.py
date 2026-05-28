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
    """Config flow DiyHome — salta la dialog credenziali impostando flow_impl direttamente."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Imposta l'implementazione OAuth2 direttamente e avvia il redirect."""
        self.flow_impl = LocalOAuth2Implementation(
            self.hass,
            DOMAIN,
            OAUTH2_CLIENT_ID,
            OAUTH2_CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        )
        return await self.async_step_auth()
