"""Config flow DiyHome — email + password."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import CLOUD_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow per DiyHome — autenticazione con email e password."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Primo passo: form email + password."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        if user_input is not None:
            email: str = user_input[CONF_EMAIL].strip().lower()
            password: str = user_input[CONF_PASSWORD]

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{CLOUD_URL}/api/ha/login",
                        json={"email": email, "password": password},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return self.async_create_entry(
                                title=email,
                                data={
                                    "access_token": data["access_token"],
                                    "email": email,
                                },
                            )
                        if resp.status in (401, 403):
                            errors["base"] = "invalid_auth"
                        else:
                            errors["base"] = "cannot_connect"

            except aiohttp.ClientConnectorError:
                errors["base"] = "cannot_connect"
            except aiohttp.ServerTimeoutError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("DiyHome login error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
