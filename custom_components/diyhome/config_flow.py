"""OAuth2 Config Flow for DiyHome — relay via diyhome.cloud."""
from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    LocalOAuth2Implementation,
)

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET, OAUTH2_TOKEN

_LOGGER = logging.getLogger(__name__)

CLOUD_CALLBACK_URI = "https://diyhome.cloud/api/ha/oauth/callback"


class DiyHomeLocalOAuth2Implementation(LocalOAuth2Implementation):
    """Implementazione OAuth2 con relay via cloud diyhome.cloud.

    Il redirect_uri punta sempre a https://diyhome.cloud/api/ha/oauth/callback
    (sempre raggiungibile, indipendentemente dalla rete dell'utente).
    L'URL dell'istanza HA (ha_url) viene aggiunto come parametro extra alla URL
    di autorizzazione: il backend lo salva nel DB e lo usa per reindirizzare il
    browser verso {ha_url}/auth/external/callback dopo il consenso.
    """

    @property
    def redirect_uri(self) -> str:
        """Usa sempre il relay cloud come redirect_uri."""
        return CLOUD_CALLBACK_URI

    async def async_get_authorize_url(self, flow_id: str) -> str:
        """Aggiunge ha_url alla URL di autorizzazione."""
        url = await super().async_get_authorize_url(flow_id)

        ha_url = (
            getattr(self.hass.config, "external_url", None)
            or getattr(self.hass.config, "internal_url", None)
        )
        if not ha_url:
            try:
                api = getattr(self.hass.config, "api", None)
                if api:
                    ha_url = getattr(api, "base_url", None)
            except Exception:
                pass

        if ha_url:
            _LOGGER.debug("DiyHome OAuth: ha_url=%s", ha_url)
            url = f"{url}&ha_url={urllib.parse.quote(ha_url, safe='')}"
        else:
            _LOGGER.warning(
                "DiyHome OAuth: ha_url non rilevato — il relay non potrà reindirizzare a HA. "
                "Configura external_url o internal_url in HA."
            )

        return url


class DiyHomeOAuth2FlowHandler(
    AbstractOAuth2FlowHandler,
    domain=DOMAIN,
):
    """Config flow DiyHome — OAuth2 con relay cloud."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Avvia il flow OAuth2."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        return await self.async_step_auth()

    async def async_oauth_create_entry(self, data: dict) -> dict:
        """Crea la config entry."""
        return self.async_create_entry(title="DiyHome Cloud", data=data)
