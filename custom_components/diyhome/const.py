"""Constants for the DiyHome integration."""

DOMAIN = "diyhome"

PLATFORMS = ["switch", "sensor", "binary_sensor"]

CLOUD_URL = "https://diyhome.cloud"

# Options keys — hostname mDNS (stabile anche dopo riavvio router)
CONF_MDNS_HOSTNAME = "mdns_hostname"  # es. "DIYHome_WT1_AABBCC" → .local risolve l'IP corrente

# Options keys — broker MQTT locale (Ondata D Local Connector / mosquitto)
CONF_MQTT_HOST     = "mqtt_host"      # IP/hostname broker locale, es. "192.168.1.10"
CONF_MQTT_PORT     = "mqtt_port"      # Default 1883
CONF_MQTT_USERNAME = "mqtt_username"  # Opzionale
CONF_MQTT_PASSWORD = "mqtt_password"  # Opzionale

# Intervalli
LAN_SCAN_INTERVAL   = 10   # secondi watchdog HTTP LAN
CLOUD_SCAN_INTERVAL = 30   # secondi polling REST cloud (emergenza)
# FIX L3 (Bug C): timeout probe alzato da 3s a 5s.
# Su HA installato come Docker container, la risoluzione mDNS di ".local" hostname
# passa dal proxy mDNS di HA e può richiedere 3-4s in condizioni normali.
# Con 3s il probe poteva fallire anche con device perfettamente raggiungibile.
LAN_CONNECT_TIMEOUT = 5    # secondi timeout probe LAN (era 3 — troppo corto su Docker)
LAN_RETRY_INTERVAL  = 60   # secondi tra retry LAN (dopo il primo a 5s — vedi _lan_retry_loop)
