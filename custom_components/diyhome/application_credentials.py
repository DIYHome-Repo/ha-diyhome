"""Application credentials platform for DiyHome.

HA 2025+ richiede che le integrazioni OAuth2 implementino
async_get_authorization_server in questo file.
Le credenziali (client_id/secret) vengono pre-importate
in async_setup() di __init__.py tramite async_import_client_credential,
quindi l'utente non deve inserire nulla manualmente.
"""
from __future__ import annotations

from homeassistant.components.application_credentials import AuthorizationServer
from homeassistant.core import HomeAssistant

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Restituisce gli endpoint OAuth2 di DiyHome."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=OAUTH2_TOKEN,
    )
