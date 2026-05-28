"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging

from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    LocalOAuth2Implementation,
    async_register_implementation,
)

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET

_LOGGER = logging.getLogger(__name__)


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """DiyHome OAuth2 config flow.

    Registers a LocalOAuth2Implementation directly before the super()
    call so HA never shows the 'add credentials' form.  This works
    for HACS custom components where async_get_application_credentials
    is not scanned at startup like built-in integrations.
    """

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(self, user_input=None):
        """Register built-in OAuth2 implementation and start flow."""
        await async_register_implementation(
            self.hass,
            LocalOAuth2Implementation(
                self.hass,
                DOMAIN,
                OAUTH2_CLIENT_ID,
                OAUTH2_CLIENT_SECRET,
                OAUTH2_AUTHORIZE,
                OAUTH2_TOKEN,
            ),
        )
        return await super().async_step_user(user_input)

    async def async_step_reauth(self, entry_data):
        """Re-authenticate with DiyHome."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm re-authentication."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
