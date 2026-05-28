"""Application credentials — non utilizzato.

L'implementazione OAuth2 viene registrata direttamente in async_setup
tramite config_entry_oauth2_flow.async_register_implementation con
LocalOAuth2Implementation, evitando la dialog 'Aggiungi credenziali'.
"""
