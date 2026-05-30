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

# How often the coordinator polls the controller while in polling mode.
# WebSocket subscription (a later stage) will reduce reliance on this.
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=15)

# Manufacturer string used for device registry entries.
MANUFACTURER: Final = "Snap One"
