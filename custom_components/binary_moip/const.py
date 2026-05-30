"""Constants for the Binary MoIP integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "binary_moip"

# Platforms this integration sets up.
PLATFORMS: Final = ["media_player"]

# Configuration / config entry keys.
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_VERIFY_SSL: Final = "verify_ssl"

# Defaults.
DEFAULT_PORT: Final = 443
DEFAULT_VERIFY_SSL: Final = False
DEFAULT_NAME: Final = "Binary MoIP"

# REST API paths (relative to base_url). See docs/naming-and-discovery.md.
API_LOGIN: Final = "/api/v1/base/auth/login"
API_UNIT_LIST: Final = "/api/v1/moip/unit"
API_UNIT: Final = "/api/v1/moip/unit/{id}"
API_GROUP_RX_LIST: Final = "/api/v1/moip/group_rx"
API_GROUP_RX: Final = "/api/v1/moip/group_rx/{id}"
API_GROUP_TX_LIST: Final = "/api/v1/moip/group_tx"
API_GROUP_TX: Final = "/api/v1/moip/group_tx/{id}"
API_AUDIO_RX: Final = "/api/v1/moip/audio_rx/{id}"

# Options-flow keys. The integration discovers everything; HA is the
# friendly-name + enable/disable layer. See docs/naming-and-discovery.md.
#   options[OPT_ZONES][<group_rx id>]   = {"enabled": bool, "label": str}
#   options[OPT_SOURCES][<group_tx id>] = {"enabled": bool, "label": str}
OPT_ZONES: Final = "zones"
OPT_SOURCES: Final = "sources"
OPT_ENABLED: Final = "enabled"
OPT_LABEL: Final = "label"

# MoIP State enum values (audio_rx / group status).
STATE_UNCONNECTED: Final = "unconnected"
STATE_STOPPED: Final = "stopped"
STATE_DETECTING: Final = "detecting"
STATE_STREAMING: Final = "streaming"
STATE_UNSUPPORTED: Final = "unsupported"
STATE_UPGRADING: Final = "upgrading"
STATE_UNKNOWN: Final = "unknown"

# How often the coordinator polls the controller while in polling mode.
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=15)

# Fallback poll interval once the change-event websocket provides real-time
# push; polling then only guards against a dropped socket.
FALLBACK_SCAN_INTERVAL: Final = timedelta(minutes=5)

# Manufacturer string used for device registry entries.
MANUFACTURER: Final = "Snap One"
