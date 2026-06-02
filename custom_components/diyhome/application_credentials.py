"""Application credentials — non usato.

DiyHome non usa il sistema application_credentials di HA.
Il config flow gestisce il token OAuth2 direttamente tramite
DiyHomeConfigFlow (config_entries.ConfigFlow), senza
AbstractOAuth2FlowHandler.
"""
