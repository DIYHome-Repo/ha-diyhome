"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging
from typing import Any

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

    Registra LocalOAuth2Implementation direttamente in async_step_user
    (approccio HACS-safe, senza dipendenza application_credentials).
    VERSION = 1 è richiesto esplicitamente da HA 2024.x.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Registra implementazione OAuth2 e avvia il flow."""
        # Registra solo se non è già presente (evita duplicati su re-entry)
        from homeassistant.helpers.config_entry_oauth2_flow import (
            async_get_implementations,
        )
        implementations = await async_get_implementations(self.hass, DOMAIN)
        if not implementations:
            # HA 2025.x: async_register_implementation(hass, domain, implementation)
            # Il DOMAIN è ora argomento separato (breaking change vs HA 2024.x)
            async_register_implementation(
                self.hass,
                DOMAIN,
                LocalOAuth2Implementation(
                    self.hass,
                    DOMAIN,
                    OAUTH2_CLIENT_ID,
                    OAUTH2_CLIENT_SECRET,
                    OAUTH2_AUTHORIZE,
                    OAUTH2_TOKEN,
                ),
            )
        # Chiama direttamente async_step_pick_implementation
        # (più sicuro di super().async_step_user() che varia tra versioni HA)
        return await self.async_step_pick_implementation(user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        """Re-autentica con DiyHome."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Conferma re-autenticazione."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
