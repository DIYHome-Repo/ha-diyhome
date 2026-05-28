"""OAuth2 Config Flow for DiyHome."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers import config_entry_oauth2_flow
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

    Registra LocalOAuth2Implementation sia qui (per prima installazione, quando
    async_setup potrebbe non essere ancora stato chiamato) sia in async_setup()
    di __init__.py (per restart successivi). Poi imposta self.flow_impl direttamente
    e salta async_step_pick_implementation per evitare dialog inutili.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Registra l'implementazione OAuth2 e avvia il redirect."""
        impl = LocalOAuth2Implementation(
            self.hass,
            DOMAIN,
            OAUTH2_CLIENT_ID,
            OAUTH2_CLIENT_SECRET,
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        )
        # Registra nel registry globale (necessario per async_get_config_entry_implementation
        # che viene chiamato in async_setup_entry dopo il completamento del flow)
        config_entry_oauth2_flow.async_register_implementation(self.hass, DOMAIN, impl)

        # Imposta direttamente flow_impl per bypassare async_step_pick_implementation
        # ed evitare la dialog "Aggiungi credenziali applicazione"
        self.flow_impl = impl
        return await self.async_step_auth()
