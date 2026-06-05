"""Backing-player transport + metadata proxying for source media_players.

A source with a configured backing media_player gains transport + now-playing
(proxied from that backing entity); a source without one stays grouping-only.
Volume is never proxied.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.binary_moip.api import MoIPSource, MoIPTopology, MoIPUnit, MoIPZone
from custom_components.binary_moip.const import DOMAIN

USER_INPUT = {"host": "ctrl.local", "port": 443, "username": "u", "password": "p", "verify_ssl": False}

ZONE, SRC_BACKED, SRC_PLAIN = 11, 41, 42
BACKING = "media_player.streaming_1"


def _topology() -> MoIPTopology:
    return MoIPTopology(
        units={3: MoIPUnit(unit_id=3, name="Main Amp", model="EA-MOIP-AMP-12D-100")},
        zones={ZONE: MoIPZone(group_id=ZONE, name="Living Room", unit_id=3, audio_rx_id=21, state="stopped")},
        sources={
            SRC_BACKED: MoIPSource(group_id=SRC_BACKED, name="HA Streaming 1", unit_id=3, hw_label="Analog Input 1", input_type="analog"),
            SRC_PLAIN: MoIPSource(group_id=SRC_PLAIN, name="HA Streaming 2", unit_id=3, hw_label="Analog Input 2", input_type="analog"),
        },
    )


async def _setup(hass):
    """Set up with SRC_BACKED mapped to BACKING; SRC_PLAIN has no backing."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="ctrl.local",
        options={"sources": {str(SRC_BACKED): {"backing_entity": BACKING}}},
    )
    entry.add_to_hass(hass)

    client = MagicMock()
    client.authenticate = AsyncMock()
    client.async_discover = AsyncMock(return_value=_topology())
    client.async_select_source = AsyncMock()
    parked = asyncio.Event()

    async def _never_connects():
        await parked.wait()

    client.async_ws_connect = AsyncMock(side_effect=_never_connects)
    with patch("custom_components.binary_moip.BinaryMoIPClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    eids = {
        "backed": reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_source_{SRC_BACKED}"),
        "plain": reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_source_{SRC_PLAIN}"),
    }
    return entry, eids


def _entity(hass, entity_id):
    """Fetch the live entity object (to call its proxy methods directly)."""
    return hass.data["entity_components"]["media_player"].get_entity(entity_id)


async def _set_backing(hass, state, attrs):
    hass.states.async_set(BACKING, state, attrs)
    await hass.async_block_till_done()


# --- supported_features gating ----------------------------------------------


async def test_backed_source_advertises_transport(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    feats = hass.states.get(eids["backed"]).attributes["supported_features"]
    for bit in (F.GROUPING, F.PLAY, F.PAUSE, F.STOP, F.NEXT_TRACK, F.PREVIOUS_TRACK):
        assert feats & bit
    assert not (feats & F.SEEK)  # backing not present/seekable yet


async def test_plain_source_is_grouping_only(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    feats = hass.states.get(eids["plain"]).attributes["supported_features"]
    assert feats == F.GROUPING


async def test_seek_added_only_when_backing_supports_it(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    await _set_backing(hass, "playing", {"supported_features": int(F.PLAY | F.SEEK)})
    feats = hass.states.get(eids["backed"]).attributes["supported_features"]
    assert feats & F.SEEK


# --- metadata + play-state mirroring ----------------------------------------


async def test_metadata_and_state_mirror_backing(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    await _set_backing(
        hass,
        "playing",
        {
            "media_title": "Some Song",
            "media_artist": "Some Artist",
            "media_album_name": "Some Album",
            "media_position": 12,
            "media_duration": 200,
            "entity_picture": "/api/media_player_proxy/media_player.streaming_1",
            "supported_features": int(F.PLAY),
        },
    )
    st = hass.states.get(eids["backed"])
    assert st.state == "playing"
    assert st.attributes["media_title"] == "Some Song"
    assert st.attributes["media_artist"] == "Some Artist"
    assert st.attributes["media_album_name"] == "Some Album"
    assert st.attributes["media_position"] == 12
    assert st.attributes["media_duration"] == 200
    assert st.attributes["entity_picture"] == "/api/media_player_proxy/media_player.streaming_1"


async def test_plain_source_has_no_metadata(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    st = hass.states.get(eids["plain"])
    assert "media_title" not in st.attributes
    assert st.attributes.get("entity_picture") is None


async def test_metadata_updates_live_on_backing_change(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    await _set_backing(hass, "playing", {"media_title": "Track A", "supported_features": int(F.PLAY)})
    assert hass.states.get(eids["backed"]).attributes["media_title"] == "Track A"
    # Change the backing player; the source must reflect it with no polling.
    await _set_backing(hass, "playing", {"media_title": "Track B", "supported_features": int(F.PLAY)})
    assert hass.states.get(eids["backed"]).attributes["media_title"] == "Track B"


# --- transport proxying -----------------------------------------------------


def _called_entity_ids(call):
    eid = call.data.get("entity_id")
    return eid if isinstance(eid, list) else [eid]


@pytest.mark.parametrize(
    ("method", "service"),
    [
        ("async_media_play", "media_play"),
        ("async_media_pause", "media_pause"),
        ("async_media_stop", "media_stop"),
        ("async_media_next_track", "media_next_track"),
        ("async_media_previous_track", "media_previous_track"),
    ],
)
async def test_transport_proxies_to_backing(hass, enable_custom_integrations, method, service):
    _, eids = await _setup(hass)
    calls = async_mock_service(hass, "media_player", service)
    await getattr(_entity(hass, eids["backed"]), method)()
    assert len(calls) == 1
    assert BACKING in _called_entity_ids(calls[0])


async def test_seek_proxies_position_to_backing(hass, enable_custom_integrations):
    _, eids = await _setup(hass)
    calls = async_mock_service(hass, "media_player", "media_seek")
    await _entity(hass, eids["backed"]).async_media_seek(42)
    assert len(calls) == 1
    assert calls[0].data["seek_position"] == 42
    assert BACKING in _called_entity_ids(calls[0])


async def test_transport_on_plain_source_raises(hass, enable_custom_integrations):
    from homeassistant.exceptions import HomeAssistantError

    _, eids = await _setup(hass)
    with pytest.raises(HomeAssistantError):
        await _entity(hass, eids["plain"]).async_media_play()
