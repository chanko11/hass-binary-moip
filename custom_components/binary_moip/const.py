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
#   options[OPT_SOURCES][<group_tx id>] = {"enabled": bool, "label": str,
#                                          "backing_entity": str}
OPT_ZONES: Final = "zones"
OPT_SOURCES: Final = "sources"
OPT_ENABLED: Final = "enabled"
OPT_LABEL: Final = "label"
# Optional per-source media_player entity_id whose transport + now-playing
# metadata the source entity proxies (e.g. a Music Assistant squeezelite player).
OPT_BACKING: Final = "backing_entity"

# media_player state attribute exposing this entity's MoIP role, so consumers
# (the Lovelace card, automations, templates) can reliably tell outputs from
# inputs. Home Assistant's media_player model has no native input/output
# concept, so we surface it explicitly here.
ATTR_MOIP_ROLE: Final = "moip_role"
ROLE_ZONE: Final = "zone"  # a group_rx output (a room/zone)
ROLE_SOURCE: Final = "source"  # a group_tx input (a feed/source)

# Synthetic source-list entry that unpairs a zone (no source / silent).
SOURCE_NONE: Final = "None"

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

# --- Listening Spaces (docs/audio_spaces_spec_v1_0.md) -----------------------
# A "Listening Space" is a named, calibrated group of zones (display term;
# code identifiers stay `space`/`spaces`). Single constant for easy rename.
SPACE_TERM: Final = "Listening Space"
SPACE_TERM_PLURAL: Final = "Listening Spaces"

# Dedicated Store helper (separate from config-entry options) for space
# definitions + per-zone calibration anchors. Survives restart.
SPACES_STORAGE_KEY: Final = "binary_moip_spaces"
SPACES_STORAGE_VERSION: Final = 1

# Calibration anchors (levels) + the abstract master scale (spec §5).
LEVEL_BACKGROUND: Final = "background"
LEVEL_LISTENING: Final = "listening"
LEVEL_PARTY: Final = "party"
LEVELS: Final = (LEVEL_BACKGROUND, LEVEL_LISTENING, LEVEL_PARTY)
MASTER_MIN: Final = 0.0
MASTER_MAX: Final = 100.0
# Default master-scale positions of the three anchors (overridable per space).
DEFAULT_MASTER_POSITIONS: Final = {
    LEVEL_BACKGROUND: 20.0,
    LEVEL_LISTENING: 50.0,
    LEVEL_PARTY: 85.0,
}

# Zone control driver (how power/input/volume are realized).
DRIVER_MOIP: Final = "moip"  # set volume on the MoIP controller (default)
DRIVER_DEVICE: Final = "device"  # delegate to a downstream media_player (e.g. AVR)

# Zone routing axis (how the source's audio reaches the zone). Independent of the
# control driver: a MoIP-fed AVR is routing=moip + driver=device. Default moip.
ROUTING_MOIP: Final = "moip"  # MoIP controller routes the source (paired_tx)
ROUTING_DEVICE: Final = "device"  # the device sources its own content (non-MoIP)

# config-entry options keys for Listening Spaces structural config (authored by
# the options flow). Calibration anchor VALUES live in the dedicated Store.
OPT_SPACES: Final = "spaces"  # list of {id, name, zones, master_positions}
OPT_ZONE_CONFIGS: Final = "zone_configs"  # {group_id: {routing, driver{...}}}
# Calibration reference config: pink-noise + (optional) music reference items.
OPT_CALIBRATION: Final = "calibration"
CONF_PINK_PLAYER: Final = "pink_player"
CONF_PINK_MEDIA: Final = "pink_media"
CONF_PINK_TYPE: Final = "pink_type"
CONF_MUSIC_PLAYER: Final = "music_player"
CONF_MUSIC_MEDIA: Final = "music_media"
CONF_MUSIC_TYPE: Final = "music_type"

# calibration_play reference modes.
REF_AUTO: Final = "auto"  # pink when configured, else sample
REF_PINK: Final = "pink"
REF_SAMPLE: Final = "sample"

# zone_set actions (per-zone override within an active Space).
ZONE_ON: Final = "on"
ZONE_OFF: Final = "off"
ZONE_NUDGE: Final = "nudge"
ZONE_ACTIONS: Final = (ZONE_ON, ZONE_OFF, ZONE_NUDGE)

# Uncalibrated zone (no anchor) safe default = this fraction of native range.
# "Hold a safe default; never blast to full" (spec §4/§5).
SAFE_DEFAULT_FRACTION: Final = 0.3
