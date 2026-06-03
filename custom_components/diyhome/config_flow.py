"""Config flow DiyHome — email + password + mDNS hostname (opzionale) + zeroconf auto-discovery."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import (
    CLOUD_URL,
    CONF_MDNS_HOSTNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# FIX P1/P2: form manuale ora include hostname mDNS opzionale.
# Utenti senza zeroconf possono inserirlo manualmente per abilitare LAN mode.
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_MDNS_HOSTNAME, default=""): str,
    }
)


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow DiyHome — autenticazione cloud + auto-discovery LAN via zeroconf."""

    VERSION = 2

    def __init__(self) -> None:
        self._entry_data: dict = {}
        self._discovered_hostname: str = ""

    # ── Zeroconf auto-discovery ───────────────────────────────────────────────

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Chiamato da HA quando trova _diyhome._tcp sulla LAN."""
        uid = discovery_info.properties.get("uid", "")
        hostname = discovery_info.hostname.rstrip(".")  # es. "DIYHome_WT1_AABBCC.local"

        if not uid:
            return self.async_abort(reason="no_uid")

        # Previeni duplicati: usa l'UID come unique_id.
        # Se l'utente aveva già una installazione manuale, _abort_if_unique_id_configured
        # aggiorna il CONF_MDNS_HOSTNAME su quella voce esistente invece di duplicare.
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured(
            updates={CONF_MDNS_HOSTNAME: hostname}
        )

        self._discovered_hostname = hostname
        self.context["title_placeholders"] = {"name": f"DIYHome ({hostname})"}

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Conferma discovery zeroconf: chiede solo email+password."""
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
                                title=f"DIYHome ({self._discovered_hostname})",
                                data={
                                    "access_token": data["access_token"],
                                    "refresh_token": data.get("refresh_token", ""),
                                    "email": email,
                                    CONF_MDNS_HOSTNAME: self._discovered_hostname,
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
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            description_placeholders={"hostname": self._discovered_hostname},
            errors=errors,
        )

    # ── Setup manuale (fallback se zeroconf non disponibile) ─────────────────

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Setup manuale: form email + password + hostname mDNS opzionale."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        if user_input is not None:
            email: str = user_input[CONF_EMAIL].strip().lower()
            password: str = user_input[CONF_PASSWORD]
            mdns_hostname: str = user_input.get(CONF_MDNS_HOSTNAME, "").strip()

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{CLOUD_URL}/api/ha/login",
                        json={"email": email, "password": password},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            # FIX P2: recupera UID dal cloud per impostare unique_id.
                            # Collega questa voce manuale al device — se in futuro lo
                            # stesso device viene scoperto via zeroconf, HA non duplica.
                            uid = await self._fetch_first_uid(
                                session, data["access_token"]
                            )
                            if uid:
                                await self.async_set_unique_id(uid)
                                self._abort_if_unique_id_configured(
                                    updates={CONF_MDNS_HOSTNAME: mdns_hostname}
                                )
                            return self.async_create_entry(
                                title=email,
                                data={
                                    "access_token": data["access_token"],
                                    "refresh_token": data.get("refresh_token", ""),
                                    "email": email,
                                    CONF_MDNS_HOSTNAME: mdns_hostname,
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

    async def _fetch_first_uid(
        self, session: aiohttp.ClientSession, token: str
    ) -> str:
        """Recupera l'UID del primo device dal cloud per impostare unique_id.

        Se l'utente ha più device, prende solo il primo — la voce manuale copre
        tutti i device dell'account come nel flow precedente.
        """
        try:
            async with session.get(
                f"{CLOUD_URL}/api/ha/devices",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    devices = await resp.json()
                    if isinstance(devices, list) and devices:
                        return str(devices[0].get("uid", ""))
        except Exception:
            pass
        return ""

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return DiyHomeOptionsFlowHandler(config_entry)


class DiyHomeOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — nessuna opzione da configurare manualmente."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="init", data_schema=vol.Schema({}))
