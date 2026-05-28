"""Application credentials for DiyHome OAuth2."""
from homeassistant.components.application_credentials import (
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """Return authorization server."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=OAUTH2_TOKEN,
    )


async def async_get_application_credentials(
    hass: HomeAssistant,
) -> list[ClientCredential]:
    """Return built-in application credentials.

    These are pre-configured so the user does not need to manually
    enter OAuth client ID/secret — they only see the DiyHome login page.
    """
    return [
        ClientCredential(
            client_id=OAUTH2_CLIENT_ID,
            client_secret=OAUTH2_CLIENT_SECRET,
            name="DiyHome Cloud",
        )
    ]
