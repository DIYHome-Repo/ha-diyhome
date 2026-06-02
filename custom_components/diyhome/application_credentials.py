"""Application credentials — non usato.

L'implementazione OAuth2 viene registrata direttamente in
DiyHomeOAuth2FlowHandler.async_step_user tramite
config_entry_oauth2_flow.async_register_implementation,
evitando la dialog 'Aggiungi credenziali' e garantendo che
sia disponibile anche se async_setup non è stato chiamato in anticipo.
"""
