"""Config Flow for DiyHome — AbstractOAuth2FlowHandler ufficiale HA."""
from __future__ import annotations

import logging

from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Config flow OAuth2 DiyHome — segue il pattern ufficiale HA.

    La registrazione dell'implementazione (client_id/secret + URLs)
    avviene in async_setup() di __init__.py tramite
    async_register_implementation() + LocalOAuth2Implementation.
    """

    DOMAIN = DOMAIN

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER
