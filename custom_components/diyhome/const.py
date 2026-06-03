"""Constants for the DiyHome integration."""

DOMAIN = "diyhome"

PLATFORMS = ["switch", "sensor", "binary_sensor"]

CLOUD_URL = "https://diyhome.cloud"

# Options keys — hostname mDNS (stabile anche dopo riavvio router)
CONF_MDNS_HOSTNAME = "mdns_hostname"  # es. "DIYHome_WT1_AABBCC" → .local risolve l'IP corrente

# Intervalli
LAN_SCAN_INTERVAL   = 10   # secondi watchdog HTTP LAN
CLOUD_SCAN_INTERVAL = 30   # secondi polling REST cloud (emergenza)
LAN_CONNECT_TIMEOUT = 3    # secondi timeout probe LAN
LAN_RETRY_INTERVAL  = 60   # secondi tra retry discovery LAN quando in cloud mode
