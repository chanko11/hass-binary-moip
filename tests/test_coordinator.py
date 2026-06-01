"""Tests for the DataUpdateCoordinator: data load, error mapping, and the
websocket change-event consumer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import WSMsgType

from custom_components.binary_moip import coordinator as coord_mod
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.binary_moip.api import (
    BinaryMoIPAuthError,
    BinaryMoIPError,
    MoIPTopology,
    MoIPZone,
)
from custom_components.binary_moip.const import DOMAIN
from custom_components.binary_moip.coordinator import BinaryMoIPDataUpdateCoordinator


def _make_coordinator(hass, client):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "ctrl.local"})
    entry.add_to_hass(hass)
    return BinaryMoIPDataUpdateCoordinator(hass, entry, client), entry


# --- _async_update_data -----------------------------------------------------


async def test_update_data_returns_topology(hass):
    topo = MoIPTopology(zones={11: MoIPZone(group_id=11, name="Kitchen")})
    client = MagicMock()
    client.async_discover = AsyncMock(return_value=topo)
    coord, _ = _make_coordinator(hass, client)

    result = await coord._async_update_data()
    assert result is topo


async def test_update_data_auth_error_becomes_update_failed(hass):
    client = MagicMock()
    client.async_discover = AsyncMock(side_effect=BinaryMoIPAuthError("bad token"))
    coord, _ = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_update_data_generic_error_becomes_update_failed(hass):
    client = MagicMock()
    client.async_discover = AsyncMock(side_effect=BinaryMoIPError("boom"))
    coord, _ = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


# --- _ws_consume ------------------------------------------------------------


class FakeWS:
    """Async-iterable websocket yielding a fixed list of messages."""

    def __init__(self, messages):
        self._messages = messages

    def __aiter__(self):
        async def gen():
            for m in self._messages:
                yield m

        return gen()


def _text(payload):
    return SimpleNamespace(type=WSMsgType.TEXT, json=lambda: payload)


def _raising_text():
    def _raise():
        raise ValueError("not json")

    return SimpleNamespace(type=WSMsgType.TEXT, json=_raise)


async def _consume(hass, messages):
    client = MagicMock()
    coord, _ = _make_coordinator(hass, client)
    coord.async_request_refresh = AsyncMock()
    await coord._ws_consume(FakeWS(messages))
    return coord.async_request_refresh


async def test_ws_consume_refreshes_on_relevant_moip_change(hass):
    refresh = await _consume(
        hass,
        [_text({"changes": [{"kind": "modified", "url": "/api/v1/moip/group_rx/11"}]})],
    )
    refresh.assert_awaited_once()


async def test_ws_consume_ignores_non_moip_url(hass):
    refresh = await _consume(
        hass,
        [_text({"changes": [{"kind": "modified", "url": "/api/v1/base/system"}]})],
    )
    refresh.assert_not_awaited()


async def test_ws_consume_ignores_unknown_kind(hass):
    refresh = await _consume(
        hass,
        [_text({"changes": [{"kind": "pinged", "url": "/api/v1/moip/group_rx/11"}]})],
    )
    refresh.assert_not_awaited()


async def test_ws_consume_skips_malformed_json(hass):
    # Should not raise, and should not refresh.
    refresh = await _consume(hass, [_raising_text()])
    refresh.assert_not_awaited()


async def test_ws_consume_ignores_non_text_frames(hass):
    refresh = await _consume(
        hass, [SimpleNamespace(type=WSMsgType.PING, json=lambda: {})]
    )
    refresh.assert_not_awaited()


async def test_ws_consume_breaks_on_close(hass):
    # A CLOSE frame stops consumption; the later change must NOT trigger refresh.
    refresh = await _consume(
        hass,
        [
            SimpleNamespace(type=WSMsgType.CLOSE, json=lambda: {}),
            _text({"changes": [{"kind": "modified", "url": "/api/v1/moip/x"}]}),
        ],
    )
    refresh.assert_not_awaited()


async def test_ws_consume_coalesces_multiple_changes_in_one_message(hass):
    refresh = await _consume(
        hass,
        [
            _text(
                {
                    "changes": [
                        {"kind": "modified", "url": "/api/v1/base/x"},
                        {"kind": "added", "url": "/api/v1/moip/audio_rx/21"},
                    ]
                }
            )
        ],
    )
    refresh.assert_awaited_once()  # one refresh for the whole message


# --- _ws_listen (connect / reconnect loop) ----------------------------------


def _patch_module_sleep(monkeypatch, sleeps):
    """Replace ONLY the coordinator module's ``asyncio`` reference so the backoff
    sleep raises CancelledError (ending the loop) without touching the real
    asyncio module — patching the global module pollutes every later test."""

    async def fake_sleep(delay):
        sleeps.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(
        coord_mod,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep, CancelledError=asyncio.CancelledError),
    )


async def test_ws_listen_connects_consumes_then_sleeps(hass, monkeypatch):
    """Happy path: connect, consume to completion, then back off before retry.

    We trip a CancelledError out of the backoff sleep to end the otherwise
    infinite loop after exactly one iteration.
    """
    client = MagicMock()
    client.async_ws_connect = AsyncMock(return_value=FakeWS([]))  # empty -> consume returns
    coord, _ = _make_coordinator(hass, client)

    sleeps: list[float] = []
    _patch_module_sleep(monkeypatch, sleeps)

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_listen()

    client.async_ws_connect.assert_awaited_once()
    assert coord.ws_connected is False           # reset in finally
    assert sleeps == [coord_mod._WS_BACKOFF_START]


async def test_ws_listen_backs_off_when_connect_fails(hass, monkeypatch):
    client = MagicMock()
    client.async_ws_connect = AsyncMock(side_effect=OSError("refused"))
    coord, _ = _make_coordinator(hass, client)
    _patch_module_sleep(monkeypatch, [])

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_listen()
    client.async_ws_connect.assert_awaited_once()


async def test_ws_listen_closes_socket_on_cancellation(hass):
    """Cancelling while consuming closes the socket and propagates CancelledError."""
    ws = MagicMock()
    ws.close = AsyncMock()
    client = MagicMock()
    client.async_ws_connect = AsyncMock(return_value=ws)
    coord, _ = _make_coordinator(hass, client)

    async def _blocking_consume(_ws):
        raise asyncio.CancelledError

    coord._ws_consume = _blocking_consume

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_listen()
    ws.close.assert_awaited_once()
    assert coord.ws_connected is False


def test_start_websocket_spawns_background_task(hass):
    client = MagicMock()
    coord, entry = _make_coordinator(hass, client)
    entry.async_create_background_task = MagicMock()
    coord._ws_listen = MagicMock(return_value=None)  # avoid creating a real coroutine
    coord.start_websocket()
    entry.async_create_background_task.assert_called_once()
