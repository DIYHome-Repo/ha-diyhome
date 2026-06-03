"""Config flow DiyHome — email + password + IP LAN opzionale."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import (
    CLOUD_URL,
    CONF_DEVICE_IP,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _lan_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_DEVICE_IP, default=d.get(CONF_DEVICE_IP, "")): str,
        }
    )


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow per DiyHome — autenticazione + IP device LAN opzionale."""

    VERSION = 1

    def __init__(self) -> None:
        self._entry_data: dict = {}

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
                            self._entry_data = {
                                "access_token": data["access_token"],
                                "email": email,
                            }
                            return await self.async_step_lan()
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

    async def async_step_lan(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Secondo passo (opzionale): IP device in LAN per modalità locale."""
        errors: dict[str, str] = {}

        if user_input is not None:
            device_ip = (user_input.get(CONF_DEVICE_IP) or "").strip()

            options: dict = {CONF_DEVICE_IP: device_ip}
            return self.async_create_entry(
                title=self._entry_data["email"],
                data=self._entry_data,
                options=options,
            )

        return self.async_show_form(
            step_id="lan",
            data_schema=_lan_schema(),
            errors=errors,
            last_step=True,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return DiyHomeOptionsFlowHandler(config_entry)


class DiyHomeOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — riconfigura IP LAN senza re-autenticare."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        return await self.async_step_lan(user_input)

    async def async_step_lan(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        current = dict(self._config_entry.options)

        if user_input is not None:
            device_ip = (user_input.get(CONF_DEVICE_IP) or "").strip()
            new_opts = dict(current)
            new_opts[CONF_DEVICE_IP] = device_ip
            return self.async_create_entry(data=new_opts)

        return self.async_show_form(
            step_id="lan",
            data_schema=_lan_schema(current),
            errors=errors,
        )
