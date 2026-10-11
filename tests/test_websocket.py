"""Tests for the Listening Spaces websocket read-API.

Covers the one-shot command and the subscription that replaced the card's
fetch-once-and-go-stale behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.binary_moip import websocket as ws
from custom_components.binary_moip.const import DOMAIN


def _manager(spaces: list[dict] | None = None) -> MagicMock:
    manager = MagicMock()
    manager.describe.return_value = spaces if spaces is not None else [{"id": "s1"}]
    return manager


def _hass(coordinator=None) -> MagicMock:
    """hass whose single config entry carries ``coordinator`` as runtime_data."""
    hass = MagicMock()
    entries = [SimpleNamespace(runtime_data=coordinator)] if coordinator is not None else []
    hass.config_entries.async_entries.return_value = entries
    return hass


def _coordinator(manager=None) -> MagicMock:
    coord = MagicMock()
    coord.space_manager = manager
    # DataUpdateCoordinator.async_add_listener returns the unsubscribe callable.
    coord._listeners = []

    def add_listener(cb):
        coord._listeners.append(cb)
        return lambda: coord._listeners.remove(cb)

    coord.async_add_listener.side_effect = add_listener
    return coord


def _connection() -> MagicMock:
    conn = MagicMock()
    conn.subscriptions = {}
    return conn


# --- one-shot: binary_moip/spaces -------------------------------------------


def test_spaces_returns_model():
    coord = _coordinator(_manager([{"id": "kitchen"}]))
    conn = _connection()
    ws.ws_spaces(_hass(coord), conn, {"id": 7})

    conn.send_result.assert_called_once_with(7, {"spaces": [{"id": "kitchen"}]})
    conn.send_error.assert_not_called()


def test_spaces_errors_when_not_set_up():
    conn = _connection()
    ws.ws_spaces(_hass(None), conn, {"id": 7})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "not_found"
    conn.send_result.assert_not_called()


def test_spaces_errors_when_entry_has_no_manager():
    """An entry mid-setup has runtime_data but no space_manager yet."""
    conn = _connection()
    ws.ws_spaces(_hass(_coordinator(None)), conn, {"id": 7})

    conn.send_error.assert_called_once()
    conn.send_result.assert_not_called()


# --- subscription: binary_moip/spaces/subscribe -----------------------------


def test_subscribe_acks_and_pushes_initial_state():
    coord = _coordinator(_manager([{"id": "s1"}]))
    conn = _connection()
    ws.ws_spaces_subscribe(_hass(coord), conn, {"id": 11})

    # acknowledged, listener registered, and the current model sent immediately
    conn.send_result.assert_called_once_with(11)
    assert coord.async_add_listener.call_count == 1
    assert conn.send_message.call_count == 1


def test_subscribe_pushes_again_on_coordinator_refresh():
    manager = _manager([{"id": "s1"}])
    coord = _coordinator(manager)
    conn = _connection()
    ws.ws_spaces_subscribe(_hass(coord), conn, {"id": 11})
    assert conn.send_message.call_count == 1  # initial

    manager.describe.return_value = [{"id": "s1", "active": True}]
    for cb in list(coord._listeners):  # simulate a coordinator update
        cb()

    assert conn.send_message.call_count == 2
    # the pushed payload reflects the *new* model, not the cached first read
    sent = conn.send_message.call_args[0][0]
    assert sent["event"] == {"spaces": [{"id": "s1", "active": True}]}


def test_subscribe_registers_unsubscribe_for_cleanup():
    coord = _coordinator(_manager())
    conn = _connection()
    ws.ws_spaces_subscribe(_hass(coord), conn, {"id": 11})

    assert 11 in conn.subscriptions
    assert len(coord._listeners) == 1
    conn.subscriptions[11]()          # HA calls this when the client goes away
    assert coord._listeners == []     # listener detached, no leak


def test_subscribe_errors_when_not_set_up():
    conn = _connection()
    ws.ws_spaces_subscribe(_hass(None), conn, {"id": 11})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "not_found"
    conn.send_result.assert_not_called()
    assert conn.subscriptions == {}


# --- registration -----------------------------------------------------------


def test_register_registers_both_commands(monkeypatch):
    registered = []
    monkeypatch.setattr(
        ws.websocket_api, "async_register_command", lambda hass, fn: registered.append(fn)
    )
    ws.async_register_ws(MagicMock())

    assert ws.ws_spaces in registered
    assert ws.ws_spaces_subscribe in registered
    assert ws.WS_SPACES == f"{DOMAIN}/spaces"
    assert ws.WS_SPACES_SUBSCRIBE == f"{DOMAIN}/spaces/subscribe"
