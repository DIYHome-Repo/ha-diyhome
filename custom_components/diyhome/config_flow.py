"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging

from homeassistant.components.application_credentials import (
    async_import_client_credential,
    ClientCredential,
)
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET

_LOGGER = logging.getLogger(__name__)


class DiyHomeOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Handle DiyHome OAuth2 authorization flow."""

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(self, user_input=None):
        """Import built-in credentials and start OAuth flow.

        async_import_client_credential stores the credential in HA's
        application_credentials storage so the credential form is skipped.
        """
        try:
            await async_import_client_credential(
                self.hass,
                DOMAIN,
                ClientCredential(
                    client_id=OAUTH2_CLIENT_ID,
                    client_secret=OAUTH2_CLIENT_SECRET,
                    name="DiyHome Cloud",
                ),
            )
        except Exception as err:
            _LOGGER.debug("Credential pre-import: %s", err)
        return await super().async_step_user(user_input)

    async def async_step_reauth(self, entry_data):
        """Re-authenticate with DiyHome."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Confirm re-authentication."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
