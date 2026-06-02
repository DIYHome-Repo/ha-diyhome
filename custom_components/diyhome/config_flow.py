"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Config flow DiyHome — OAuth2 standard HA."""

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
        return await self.async_step_auth()

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Crea la config entry con titolo custom."""
        return self.async_create_entry(title="DiyHome Cloud", data=data)
