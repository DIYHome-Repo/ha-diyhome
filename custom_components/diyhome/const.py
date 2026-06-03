"""Constants for the DiyHome integration."""

DOMAIN = "diyhome"

PLATFORMS = ["switch", "sensor", "binary_sensor"]

CLOUD_URL = "https://diyhome.cloud"

# Options keys — broker MQTT locale
CONF_MQTT_ENABLED  = "mqtt_enabled"
CONF_MQTT_HOST     = "mqtt_host"
CONF_MQTT_PORT     = "mqtt_port"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_MQTT_TLS      = "mqtt_tls"

DEFAULT_MQTT_PORT  = 1883
