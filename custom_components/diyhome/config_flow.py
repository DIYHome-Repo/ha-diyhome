"""Config flow DiyHome — email + password + mDNS hostname (opzionale) + zeroconf auto-discovery."""
from __future__ import annotations

import logging
import re

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

# Regex per rilevare un IP puro (es. "192.168.1.248")
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_MDNS_HOSTNAME, default=""): str,
    }
)


async def _resolve_ip_to_local_hostname(
    session: aiohttp.ClientSession, host: str
) -> str:
    """Se host è un IP puro, interroga /mdns-state sul device per ottenere
    il nome .local stabile che non cambia dopo reset router.

    Ritorna l'hostname .local se trovato, altrimenti ritorna host invariato.
    Il device espone /mdns-state senza autenticazione (endpoint pubblico).
    """
    if not host or not _IP_RE.match(host):
        return host  # già hostname .local o vuoto — non modificare

    try:
        async with session.get(
            f"http://{host}/mdns-state",
            timeout=aiohttp.ClientTimeout(total=3),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                hostname = data.get("hostname", "").strip()
                if hostname:
                    resolved = (
                        hostname
                        if hostname.endswith(".local")
                        else f"{hostname}.local"
                    )
                    _LOGGER.debug(
                        "DiyHome: IP %s → hostname mDNS stabile %s", host, resolved
                    )
                    return resolved
    except Exception as err:
        _LOGGER.debug(
            "DiyHome: /mdns-state su %s non raggiungibile (%s) — uso IP come fallback",
            host,
            err,
        )

    return host  # fallback: usa IP


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
        # Se l'utente aveva già una installazione manuale con IP, _abort_if_unique_id_configured
        # aggiorna il CONF_MDNS_HOSTNAME su quella voce esistente con l'hostname .local corretto.
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
        """Setup manuale: form email + password + hostname mDNS opzionale.

        Se l'utente inserisce un IP (es. 192.168.1.248), il sistema interroga
        automaticamente il device per ottenere il nome .local stabile e salva
        quello — così l'integrazione non si rompe dopo un reset del router.
        """
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        errors: dict[str, str] = {}

        if user_input is not None:
            email: str = user_input[CONF_EMAIL].strip().lower()
            password: str = user_input[CONF_PASSWORD]
            raw_hostname: str = user_input.get(CONF_MDNS_HOSTNAME, "").strip()

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{CLOUD_URL}/api/ha/login",
                        json={"email": email, "password": password},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()

                            # ── Auto-risoluzione IP → hostname .local ────────
                            # Se l'utente ha inserito un IP, chiediamo al device
                            # il suo hostname mDNS stabile prima di salvarlo.
                            # Questo garantisce che dopo un reset router l'hostname
                            # .local si risolva sempre al nuovo IP automaticamente.
                            mdns_hostname = await _resolve_ip_to_local_hostname(
                                session, raw_hostname
                            )
                            if mdns_hostname != raw_hostname:
                                _LOGGER.info(
                                    "DiyHome: IP %s convertito automaticamente in hostname "
                                    "mDNS stabile %s",
                                    raw_hostname,
                                    mdns_hostname,
                                )

                            # FIX P2: recupera UID dal cloud per impostare unique_id.
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
        """Recupera l'UID del primo device dal cloud per impostare unique_id."""
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
    """Options flow — aggiorna hostname mDNS con auto-risoluzione IP→.local."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Mostra il campo hostname mDNS e auto-risolve l'IP se necessario."""
        current_hostname: str = (
            self._config_entry.options.get(CONF_MDNS_HOSTNAME)
            or self._config_entry.data.get(CONF_MDNS_HOSTNAME, "")
        )

        if user_input is not None:
            new_hostname: str = user_input.get(CONF_MDNS_HOSTNAME, "").strip()

            # Auto-risoluzione: se l'utente ha incollato un IP, convertilo subito
            if new_hostname:
                async with aiohttp.ClientSession() as session:
                    resolved = await _resolve_ip_to_local_hostname(session, new_hostname)
                    if resolved != new_hostname:
                        _LOGGER.info(
                            "DiyHome options: IP %s convertito automaticamente in %s",
                            new_hostname,
                            resolved,
                        )
                    new_hostname = resolved

            return self.async_create_entry(
                title="",
                data={CONF_MDNS_HOSTNAME: new_hostname},
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_MDNS_HOSTNAME, default=current_hostname
                    ): str,
                }
            ),
        )
