"""End-to-end config-entry setup/unload/reload, with the client fully mocked.

Exercises __init__.async_setup_entry (client + coordinator + platform forward +
websocket start), the media_player platform setup, and the options-change reload
listener — without touching a real controller.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.binary_moip.api import (
    MoIPSource,
    MoIPTopology,
    MoIPUnit,
    MoIPZone,
)
from custom_components.binary_moip.const import DOMAIN, OPT_ZONES

USER_INPUT = {
    "host": "ctrl.local",
    "port": 443,
    "username": "admin",
    "password": "secret",
    "verify_ssl": False,
}


def _topology() -> MoIPTopology:
    return MoIPTopology(
        units={3: MoIPUnit(unit_id=3, name="Main Amp", model="EA-MOIP-AMP-12D-100")},
        zones={
            11: MoIPZone(
                group_id=11,
                name="Kitchen",
                unit_id=3,
                audio_rx_id=21,
                volume=40,
                volume_range=(0.0, 100.0),
                state="streaming",
            )
        },
        sources={41: MoIPSource(group_id=41, name="TX-1", unit_id=3, hw_label="HDMI")},
    )


def _mock_client():
    """A BinaryMoIPClient mock that never reconnects its websocket (so the
    background listener stays parked instead of busy-looping)."""
    client = MagicMock()
    client.async_discover = AsyncMock(return_value=_topology())
    client.authenticate = AsyncMock(return_value=None)

    parked = asyncio.Event()  # never set -> ws_connect awaits forever, then cancelled

    async def _never_connects():
        await parked.wait()

    client.async_ws_connect = AsyncMock(side_effect=_never_connects)
    return client


async def _setup(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="ctrl.local")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.binary_moip.BinaryMoIPClient", return_value=_mock_client()
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_creates_zone_entity(hass, enable_custom_integrations):
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get("media_player.kitchen")
    assert state is not None
    assert state.attributes["volume_level"] == pytest.approx(0.4)


async def test_unload_entry(hass, enable_custom_integrations):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_options_change_reloads_entry(hass, enable_custom_integrations):
    entry = await _setup(hass)
    # Disabling the only zone via options should reload and drop the entity.
    with patch(
        "custom_components.binary_moip.BinaryMoIPClient", return_value=_mock_client()
    ):
        hass.config_entries.async_update_entry(
            entry, options={OPT_ZONES: {"11": {"enabled": False}}}
        )
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("media_player.kitchen") is None
