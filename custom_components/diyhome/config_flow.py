"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET


class DiyHomeOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Handle DiyHome OAuth2 authorization flow."""

    DOMAIN = DOMAIN

    @property
    def logger(self):
        import logging
        return logging.getLogger(__name__)

    async def async_step_user(self, user_input=None):
        """Register built-in OAuth2 implementation and start the flow.

        This registers the implementation programmatically so HA never
        shows the 'Add Application Credentials' screen to the user.
        """
        await config_entry_oauth2_flow.async_register_implementation(
            self.hass,
            DOMAIN,
            config_entry_oauth2_flow.LocalOAuth2Implementation(
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
