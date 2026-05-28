"""Constants for the DiyHome integration."""

DOMAIN = "diyhome"

PLATFORMS = ["switch", "sensor", "binary_sensor"]

CLOUD_URL = "https://diyhome.cloud"
OAUTH2_AUTHORIZE = f"{CLOUD_URL}/oauth/authorize"
OAUTH2_TOKEN = f"{CLOUD_URL}/api/ha/oauth/token"

OAUTH2_CLIENT_ID = "diyhome-ha"
