"""Websocket read-API for Listening Spaces (frontend cards read the model here).

Mutations go through services (§6); this is the read side the calibration tool
and playback card need — the Space list with zones, anchors, calibration status,
and live state.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

WS_SPACES = f"{DOMAIN}/spaces"


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the Listening Space websocket commands (idempotent)."""
    websocket_api.async_register_command(hass, ws_spaces)


@websocket_api.websocket_command({vol.Required("type"): WS_SPACES})
@callback
def ws_spaces(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return all Listening Spaces with zones/anchors/state (see SpaceManager.describe)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        manager = getattr(getattr(entry, "runtime_data", None), "space_manager", None)
        if manager is not None:
            connection.send_result(msg["id"], {"spaces": manager.describe()})
            return
    connection.send_error(msg["id"], "not_found", "Binary MoIP is not set up")
