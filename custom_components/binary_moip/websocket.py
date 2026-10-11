"""Websocket read-API for Listening Spaces (frontend cards read the model here).

Mutations go through services (§6); this is the read side the calibration tool
and playback card need — the Space list with zones, anchors, calibration status,
and live state.

Two commands:

``binary_moip/spaces``
    One-shot read. Returns the current model and nothing further. Kept for
    callers that genuinely want a snapshot.

``binary_moip/spaces/subscribe``
    Sends the model immediately, then pushes a fresh copy on every coordinator
    refresh until the client unsubscribes. Cards should use this: the model
    carries live fields (``active``, ``source``, per-zone ``online``,
    ``offline_zones``), so a one-shot read goes stale the moment an amplifier
    changes state — which is exactly when the display matters most.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

WS_SPACES = f"{DOMAIN}/spaces"
WS_SPACES_SUBSCRIBE = f"{DOMAIN}/spaces/subscribe"


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the Listening Space websocket commands (idempotent)."""
    websocket_api.async_register_command(hass, ws_spaces)
    websocket_api.async_register_command(hass, ws_spaces_subscribe)


@callback
def _find_coordinator(hass: HomeAssistant):
    """Return the first set-up coordinator that has a SpaceManager, else None.

    ``entry.runtime_data`` is the coordinator; the manager hangs off it.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if getattr(coordinator, "space_manager", None) is not None:
            return coordinator
    return None


@websocket_api.websocket_command({vol.Required("type"): WS_SPACES})
@callback
def ws_spaces(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Return all Listening Spaces with zones/anchors/state (see SpaceManager.describe)."""
    coordinator = _find_coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "Binary MoIP is not set up")
        return
    connection.send_result(msg["id"], {"spaces": coordinator.space_manager.describe()})


@websocket_api.websocket_command({vol.Required("type"): WS_SPACES_SUBSCRIBE})
@callback
def ws_spaces_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Subscribe to the Listening Space model; push on every coordinator refresh."""
    coordinator = _find_coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", "Binary MoIP is not set up")
        return

    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"spaces": coordinator.space_manager.describe()}
            )
        )

    # async_add_listener returns the unsubscribe callable; storing it in
    # connection.subscriptions makes HA detach us when the client goes away.
    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_push)
    connection.send_result(msg["id"])
    # Send the current model straight away so the client has state without also
    # issuing the one-shot command.
    _push()
