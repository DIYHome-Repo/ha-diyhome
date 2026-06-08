"""Config flow DiyHome — login cloud + auto-discovery LAN via zeroconf."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import (
    CLOUD_URL,
    CONF_MDNS_HOSTNAME,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Form setup — solo email e password, nessun campo IP/hostname
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class DiyHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow DiyHome:
    1. Zeroconf auto-discovery (HA rileva automaticamente il device sulla LAN)
    2. Setup manuale: solo email + password → il sistema cerca il device in automatico
       - login cloud → fetch UIDs → scan zeroconf 5s → hostname trovato automaticamente
       - se non trovato: cloud mode (LAN configurabile dopo da "Configura")
    """

    VERSION = 1

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
                        if uid in known_uids:
                            # Preferisce IP diretto su .local — in HA Docker/container
                            # il resolver mDNS può non funzionare per .local
                            addrs = info.parsed_addresses() or []
                            ipv4 = next((a for a in addrs if ":" not in a), None)
                            return ipv4 or (info.server.rstrip(".") if info.server else "")
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
        cur_mqtt_host: str = (
            self._config_entry.options.get(CONF_MQTT_HOST, "")
            or self._config_entry.data.get(CONF_MQTT_HOST, "")
        )
        cur_mqtt_port: int = int(
            self._config_entry.options.get(CONF_MQTT_PORT)
            or self._config_entry.data.get(CONF_MQTT_PORT, 1883)
        )
        cur_mqtt_user: str = (
            self._config_entry.options.get(CONF_MQTT_USERNAME, "")
            or self._config_entry.data.get(CONF_MQTT_USERNAME, "")
        )

        if user_input is not None:
            new_hostname: str = user_input.get(CONF_MDNS_HOSTNAME, "").strip()
            new_mqtt_host: str = user_input.get(CONF_MQTT_HOST, "").strip()
            opts: dict = {CONF_MDNS_HOSTNAME: new_hostname}
            if new_mqtt_host:
                opts[CONF_MQTT_HOST] = new_mqtt_host
                opts[CONF_MQTT_PORT] = int(user_input.get(CONF_MQTT_PORT, 1883))
                opts[CONF_MQTT_USERNAME] = user_input.get(CONF_MQTT_USERNAME, "").strip()
                raw_pass = user_input.get(CONF_MQTT_PASSWORD, "")
                # Mantieni la password esistente se il campo è vuoto (non sovrascrivere)
                if raw_pass:
                    opts[CONF_MQTT_PASSWORD] = raw_pass
                elif cur_mqtt_host == new_mqtt_host:
                    existing_pass = (
                        self._config_entry.options.get(CONF_MQTT_PASSWORD, "")
                        or self._config_entry.data.get(CONF_MQTT_PASSWORD, "")
                    )
                    opts[CONF_MQTT_PASSWORD] = existing_pass
            return self.async_create_entry(title="", data=opts)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MDNS_HOSTNAME, default=current_hostname): str,
                    vol.Optional(CONF_MQTT_HOST,     default=cur_mqtt_host):    str,
                    vol.Optional(CONF_MQTT_PORT,     default=cur_mqtt_port):    int,
                    vol.Optional(CONF_MQTT_USERNAME, default=cur_mqtt_user):    str,
                    # Password: campo vuoto = mantieni quella salvata
                    vol.Optional(CONF_MQTT_PASSWORD, default=""):              str,
                }
            ),
            description_placeholders={
                "mqtt_hint": "Lascia vuoto per disabilitare MQTT locale. "
                             "Inserisci IP/hostname del tuo broker (es. 192.168.1.10).",
            },
        )
