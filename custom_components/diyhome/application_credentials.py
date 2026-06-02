"""Application credentials — non usato da questo componente.

DiyHome registra l'implementazione OAuth2 direttamente in async_setup()
tramite config_entry_oauth2_flow.async_register_implementation() +
LocalOAuth2Implementation con credenziali hardcoded.
Nessun input utente richiesto.
"""
