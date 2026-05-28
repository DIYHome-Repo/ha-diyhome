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
    """Config flow DiyHome.

    L'implementazione OAuth2 (LocalOAuth2Implementation) viene registrata
    in async_setup() di __init__.py tramite async_register_implementation().
    Con una sola implementazione registrata, async_step_pick_implementation()
    la seleziona automaticamente senza mostrare alcuna dialog all'utente.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Avvia il flusso OAuth2 selezionando l'implementazione registrata."""
        return await self.async_step_pick_implementation(user_input)
