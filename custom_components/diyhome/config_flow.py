"""OAuth2 Config Flow for DiyHome (HA 2025.x)."""
from __future__ import annotations

import logging

from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """DiyHome OAuth2 config flow.

    Usa application_credentials.py (standard HA 2025.x).
    Le credenziali vengono pre-caricate da async_setup via async_import_client_credential.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER
