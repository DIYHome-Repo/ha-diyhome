"""Config Flow for DiyHome — OAuth2 custom, senza AbstractOAuth2FlowHandler."""
from __future__ import annotations

import aiohttp
import logging
import urllib.parse
from typing import Any

from homeassistant import config_entries
from homeassistant.helpers.config_entry_oauth2_flow import _encode_jwt

from .const import (
    CLOUD_URL,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_CLIENT_ID,
    OAUTH2_CLIENT_SECRET,
    OAUTH2_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

CLOUD_CALLBACK_URI = f"{CLOUD_URL}/api/ha/oauth/callback"


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow DiyHome con OAuth2 custom.

    Non usa AbstractOAuth2FlowHandler né application_credentials:
    gestisce il flow direttamente tramite async_external_step con
    state JWT nativo di HA. Funziona su qualsiasi versione HA.
    """

    VERSION = 1
    _code: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Entry point — avvia OAuth2."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        return await self._async_start_oauth()

    async def _async_start_oauth(self) -> dict[str, Any]:
        """Costruisce URL di autorizzazione e apre il browser."""
        ha_url = (
            getattr(self.hass.config, "external_url", None)
            or getattr(self.hass.config, "internal_url", None)
            or ""
        )

        # state JWT firmato da HA — necessario per il routing del callback
        # a /auth/external/callback verso questo flow
        state = _encode_jwt(self.hass, {"flow_id": self.flow_id})

        params: dict[str, str] = {
            "client_id": OAUTH2_CLIENT_ID,
            "response_type": "code",
            "state": state,
            "redirect_uri": CLOUD_CALLBACK_URI,
        }
        if ha_url:
            params["ha_url"] = ha_url

        url = OAUTH2_AUTHORIZE + "?" + urllib.parse.urlencode(params)
        _LOGGER.debug("DiyHome OAuth: authorize URL=%s ha_url=%s", url, ha_url or "n/d")
        return self.async_external_step(step_id="auth", url=url)

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Riceve code da /auth/external/callback."""
        if user_input is None:
            return self.async_abort(reason="oauth_error")

        code = user_input.get("code")
        if not code:
            error = user_input.get("error", "unknown")
            _LOGGER.error("DiyHome OAuth callback errore: %s", error)
            return self.async_abort(reason="oauth_error")

        self._code = code
        return self.async_external_step_done(next_step_id="create")

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Scambia code per token e crea la config entry."""
        if not self._code:
            return self.async_abort(reason="oauth_error")

        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    OAUTH2_TOKEN,
                    data={
                        "grant_type": "authorization_code",
                        "code": self._code,
                        "client_id": OAUTH2_CLIENT_ID,
                        "client_secret": OAUTH2_CLIENT_SECRET,
                        "redirect_uri": CLOUD_CALLBACK_URI,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.error(
                        "DiyHome token exchange HTTP %s: %s", resp.status, body[:200]
                    )
                    return self.async_abort(reason="oauth_error")
                token_data = await resp.json()
        except Exception as err:
            _LOGGER.error("DiyHome token exchange exception: %s", err)
            return self.async_abort(reason="oauth_error")

        return self.async_create_entry(
            title="DiyHome Cloud",
            data={"token": token_data},
        )
