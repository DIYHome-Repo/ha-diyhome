"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging

from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DiyHomeOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Handle DiyHome OAuth2 authorization flow.

    The OAuth2 implementation is registered at startup in async_setup (__init__.py),
    so HA always finds it without needing application_credentials.
    """

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_reauth(self, entry_data):
        """Re-authenticate with DiyHome."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm re-authentication."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
