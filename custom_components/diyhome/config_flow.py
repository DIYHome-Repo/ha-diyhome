"""Config flow DiyHome — login cloud + auto-discovery LAN via zeroconf."""
from __future__ import annotations

import asyncio
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

# Regex per rilevare IP puro (es. "192.168.1.248")
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# Form setup — solo email e password, nessun campo IP/hostname
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _resolve_ip_to_local_hostname(
    session: aiohttp.ClientSession, host: str
) -> str:
    """Se host è un IP puro, interroga /mdns-state sul device per ottenere
    il nome .local stabile che non cambia dopo reset router.

    Il device espone /mdns-state senza autenticazione (endpoint pubblico).
    """
    if not host or not _IP_RE.match(host):
        return host

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
    return host


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow DiyHome:
    1. Zeroconf auto-discovery (HA rileva automaticamente il device sulla LAN)
    2. Setup manuale: solo email + password → il sistema cerca il device in automatico
       - login cloud → fetch UIDs → scan zeroconf 5s → hostname trovato automaticamente
       - se non trovato: cloud mode (LAN configurabile dopo da "Configura")
    """

    VERSION = 2

    def __init__(self) -> None:
        self._entry_data: dict = {}
        self._discovered_hostname: str = ""

    # ── Zeroconf auto-discovery ───────────────────────────────────────────────

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Chiamato da HA quando trova _diyhome._tcp sulla LAN — discovery automatica."""
        uid = discovery_info.properties.get("uid", "")
        hostname = discovery_info.hostname.rstrip(".")

        if not uid:
            return self.async_abort(reason="no_uid")

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
            except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError):
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

    # ── Setup manuale — solo email + password ─────────────────────────────────

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Setup manuale: email + password → il sistema trova il device in automatico.

        Dopo il login il sistema:
        1. Recupera la lista device dall'account cloud
        2. Scansiona la LAN via zeroconf per 5s
        3. Abbina il device per UID → salva hostname .local automaticamente
        4. Se non trovato (Docker senza multicast, rete diversa): modalità cloud
           L'utente può aggiungere l'hostname dopo da Impostazioni → Configura.
        """
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
                            access_token = data["access_token"]

                            # ── Recupera UIDs device dal cloud ───────────────
                            device_uids = await self._fetch_device_uids(
                                session, access_token
                            )

                            # ── Scan zeroconf automatico (max 5s) ────────────
                            # Cerca _diyhome._tcp.local. sulla LAN e abbina
                            # per UID con i device dell'account. Zero input utente.
                            mdns_hostname = ""
                            if device_uids:
                                mdns_hostname = await self._scan_zeroconf_for_device(
                                    set(device_uids)
                                )
                                if mdns_hostname:
                                    _LOGGER.info(
                                        "DiyHome: device trovato automaticamente "
                                        "sulla LAN → %s",
                                        mdns_hostname,
                                    )
                                else:
                                    _LOGGER.info(
                                        "DiyHome: device non trovato sulla LAN — "
                                        "modalità cloud attiva. Puoi aggiungere "
                                        "l'hostname in Impostazioni → Configura."
                                    )

                            # ── Imposta unique_id dal primo device ───────────
                            first_uid = device_uids[0] if device_uids else ""
                            if first_uid:
                                await self.async_set_unique_id(first_uid)
                                self._abort_if_unique_id_configured(
                                    updates={CONF_MDNS_HOSTNAME: mdns_hostname}
                                    if mdns_hostname
                                    else {}
                                )

                            title = (
                                f"DIYHome ({mdns_hostname})"
                                if mdns_hostname
                                else email
                            )
                            return self.async_create_entry(
                                title=title,
                                data={
                                    "access_token": access_token,
                                    "refresh_token": data.get("refresh_token", ""),
                                    "email": email,
                                    CONF_MDNS_HOSTNAME: mdns_hostname,
                                },
                            )

                        if resp.status in (401, 403):
                            errors["base"] = "invalid_auth"
                        else:
                            errors["base"] = "cannot_connect"

            except (aiohttp.ClientConnectorError, aiohttp.ServerTimeoutError):
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("DiyHome setup error: %s", err)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _fetch_device_uids(
        self, session: aiohttp.ClientSession, token: str
    ) -> list[str]:
        """Recupera la lista UID device dall'account cloud."""
        try:
            async with session.get(
                f"{CLOUD_URL}/api/ha/devices",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    devices = await resp.json()
                    if isinstance(devices, list):
                        return [
                            str(d["uid"])
                            for d in devices
                            if d.get("uid")
                        ]
        except Exception:
            pass
        return []

    async def _scan_zeroconf_for_device(self, known_uids: set[str]) -> str:
        """Scansiona la LAN via zeroconf (max 5s) cercando _diyhome._tcp.local.

        Abbina il device per UID TXT record con i device dell'account.
        Ritorna l'hostname .local se trovato, stringa vuota altrimenti.
        Graceful: qualsiasi errore → ritorna "" (cloud mode).
        """
        if not known_uids:
            return ""

        try:
            from homeassistant.components.zeroconf import async_get_async_instance
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

            aiozc = await async_get_async_instance(self.hass)
            found_names: list[str] = []
            evt = asyncio.Event()

            def _handler(
                zeroconf_instance,
                service_type: str,
                name: str,
                state_change: ServiceStateChange,
            ) -> None:
                if state_change in (
                    ServiceStateChange.Added,
                    ServiceStateChange.Updated,
                ):
                    if name not in found_names:
                        found_names.append(name)
                    evt.set()

            browser = AsyncServiceBrowser(
                aiozc.zeroconf,
                "_diyhome._tcp.local.",
                handlers=[_handler],
            )

            try:
                await asyncio.wait_for(evt.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            finally:
                browser.cancel()

            # Interroga le info per ogni servizio trovato e abbina per UID
            for name in found_names:
                info = AsyncServiceInfo("_diyhome._tcp.local.", name)
                try:
                    if await info.async_request(aiozc.zeroconf, 3000):
                        props = info.properties or {}
                        uid_bytes = props.get(b"uid") or props.get("uid", b"")
                        if isinstance(uid_bytes, bytes):
                            uid = uid_bytes.decode("utf-8", errors="replace")
                        else:
                            uid = str(uid_bytes)
                        if uid in known_uids and info.server:
                            return info.server.rstrip(".")
                except Exception:
                    continue

        except Exception as err:
            _LOGGER.debug("DiyHome: zeroconf scan: %s", err)

        return ""

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return DiyHomeOptionsFlowHandler(config_entry)


class DiyHomeOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow — aggiorna hostname LAN con auto-risoluzione IP→.local."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        current_hostname: str = (
            self._config_entry.options.get(CONF_MDNS_HOSTNAME)
            or self._config_entry.data.get(CONF_MDNS_HOSTNAME, "")
        )

        if user_input is not None:
            new_hostname: str = user_input.get(CONF_MDNS_HOSTNAME, "").strip()

            if new_hostname:
                async with aiohttp.ClientSession() as session:
                    resolved = await _resolve_ip_to_local_hostname(
                        session, new_hostname
                    )
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
