"""Tests for the media_player platform.

Split into:
- pure label/disambiguation helpers (no Home Assistant runtime needed), and
- entity behavior (state/volume/source + service calls), driven by a lightweight
  fake coordinator so we exercise the entity without standing up a full hass.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.media_player import MediaPlayerState

from custom_components.binary_moip import media_player as mp
from custom_components.binary_moip.api import (
    MoIPSource,
    MoIPTopology,
    MoIPUnit,
    MoIPZone,
)
from custom_components.binary_moip.const import DOMAIN, MANUFACTURER, SOURCE_NONE


# --- _source_label ----------------------------------------------------------


def _src(**kw) -> MoIPSource:
    base = {"group_id": 1, "name": "TX-1"}
    base.update(kw)
    return MoIPSource(**base)


def test_source_label_uses_override_verbatim():
    s = _src(hw_label="Digital", unit_name="Rack", input_type="toslink")
    assert mp._source_label(s, "My Sonos") == "My Sonos"


def test_source_label_keeps_user_assigned_controller_name():
    # A real controller name (not a TX-... default) wins over the synthesized
    # unit/hw_label form, so "Record Player" stays "Record Player".
    s = _src(name="Record Player", hw_label="Audio Input", unit_name="Record Player Transmitter", input_type="analog")
    assert mp._source_label(s, None) == "Record Player"


@pytest.mark.parametrize(
    ("name", "is_default"),
    [
        ("TX-D46A9128261A-1", True),
        ("tx-000fffa11beb", True),   # case-insensitive
        ("", True),
        (None, True),
        ("Record Player", False),
        ("C4 Streaming", False),
    ],
)
def test_is_default_source_name(name, is_default):
    assert mp._is_default_source_name(name) is is_default


def test_source_label_combines_unit_hwlabel_and_input_type():
    s = _src(hw_label="Digital Input", unit_name="AV Rack", input_type="toslink")
    assert mp._source_label(s, None) == "AV Rack – Digital Input (toslink)"


def test_source_label_omits_input_type_when_already_present():
    s = _src(hw_label="Toslink In", unit_name="Rack", input_type="toslink")
    # "toslink" already appears in the label (case-insensitive) -> not appended.
    assert mp._source_label(s, None) == "Rack – Toslink In"


def test_source_label_falls_back_to_name_without_hwlabel_or_unit():
    s = _src(name="TX-9", hw_label=None, unit_name=None, input_type=None)
    assert mp._source_label(s, None) == "TX-9"


# --- _build_source_maps -----------------------------------------------------


def test_build_source_maps_disambiguates_collisions():
    data = MoIPTopology(
        sources={
            1: _src(group_id=1, hw_label="HDMI", unit_name="Streamer"),
            2: _src(group_id=2, hw_label="HDMI", unit_name="Streamer"),
            3: _src(group_id=3, hw_label="Analog", unit_name="Streamer"),
        }
    )
    labels, reverse = mp._build_source_maps(data, {})
    assert labels[1] == "Streamer – HDMI #1"
    assert labels[2] == "Streamer – HDMI #2"
    assert labels[3] == "Streamer – Analog"   # unique -> no suffix
    # reverse map round-trips
    assert reverse["Streamer – HDMI #1"] == 1
    assert reverse["Streamer – Analog"] == 3


def test_build_source_maps_applies_option_overrides():
    data = MoIPTopology(sources={5: _src(group_id=5, hw_label="HDMI")})
    options = {"sources": {"5": {"label": "Apple TV"}}}
    labels, _ = mp._build_source_maps(data, options)
    assert labels[5] == "Apple TV"


# --- entity behavior --------------------------------------------------------


class FakeCoordinator:
    """Minimal stand-in for BinaryMoIPDataUpdateCoordinator for entity tests."""

    def __init__(self, data: MoIPTopology, options: dict | None = None) -> None:
        self.data = data
        self.client = AsyncMock()
        self.last_update_success = True
        self.async_request_refresh = AsyncMock()
        self.config_entry = SimpleNamespace(entry_id="e1", options=options or {})


def _zone(**kw) -> MoIPZone:
    base = {"group_id": 11, "name": "Kitchen"}
    base.update(kw)
    return MoIPZone(**base)


def _entity(topo: MoIPTopology, group_id: int = 11, options: dict | None = None):
    coord = FakeCoordinator(topo, options)
    ent = mp.BinaryMoIPMediaPlayer(coord, group_id)
    return ent, coord


def test_state_playing_when_streaming():
    topo = MoIPTopology(zones={11: _zone(state="streaming")})
    ent, _ = _entity(topo)
    assert ent.state == MediaPlayerState.PLAYING


def test_state_idle_when_not_streaming():
    topo = MoIPTopology(zones={11: _zone(state="stopped")})
    ent, _ = _entity(topo)
    assert ent.state == MediaPlayerState.IDLE


def test_unique_id_combines_entry_and_group():
    topo = MoIPTopology(zones={11: _zone()})
    ent, _ = _entity(topo)
    assert ent.unique_id == "e1_11"


@pytest.mark.parametrize(
    ("volume", "rng", "expected"),
    [
        (50, (0.0, 100.0), 0.5),
        (50, (10.0, 90.0), 0.5),
        (10, (10.0, 90.0), 0.0),
        (90, (10.0, 90.0), 1.0),
        (200, (0.0, 100.0), 1.0),   # clamped
    ],
)
def test_volume_level_scaling(volume, rng, expected):
    topo = MoIPTopology(zones={11: _zone(volume=volume, volume_range=rng)})
    ent, _ = _entity(topo)
    assert ent.volume_level == pytest.approx(expected)


def test_volume_level_none_when_unknown():
    topo = MoIPTopology(zones={11: _zone(volume=None)})
    ent, _ = _entity(topo)
    assert ent.volume_level is None


def test_volume_level_none_for_degenerate_range():
    topo = MoIPTopology(zones={11: _zone(volume=50, volume_range=(50.0, 50.0))})
    ent, _ = _entity(topo)
    assert ent.volume_level is None


def test_is_volume_muted_reflects_zone():
    topo = MoIPTopology(zones={11: _zone(muted=True)})
    ent, _ = _entity(topo)
    assert ent.is_volume_muted is True


def test_available_false_when_unconnected():
    topo = MoIPTopology(zones={11: _zone(state="unconnected")})
    ent, _ = _entity(topo)
    assert ent.available is False


def test_available_true_when_connected_and_present():
    topo = MoIPTopology(zones={11: _zone(state="streaming")})
    ent, _ = _entity(topo)
    assert ent.available is True


def test_name_prefers_option_override():
    topo = MoIPTopology(zones={11: _zone(name="Kitchen")})
    ent, _ = _entity(topo, options={"zones": {"11": {"label": "The Kitchen"}}})
    assert ent.name == "The Kitchen"


def test_name_falls_back_to_zone_name():
    topo = MoIPTopology(zones={11: _zone(name="Kitchen")})
    ent, _ = _entity(topo)
    assert ent.name == "Kitchen"


def test_device_info_is_per_zone_with_parent_model():
    topo = MoIPTopology(
        units={3: MoIPUnit(unit_id=3, name="Main Amp", model="EA-MOIP-AMP-12D-100")},
        zones={11: _zone(unit_id=3)},
    )
    ent, _ = _entity(topo)
    info = ent.device_info
    assert (DOMAIN, "e1_11") in info["identifiers"]
    assert info["name"] == "Kitchen"
    assert info["manufacturer"] == MANUFACTURER
    assert info["model"] == "EA-MOIP-AMP-12D-100"  # carried from parent unit


def test_device_info_without_unit_has_no_model():
    topo = MoIPTopology(zones={11: _zone(unit_id=None)})
    ent, _ = _entity(topo)
    assert ent.device_info["model"] is None


# --- source / source_list ---------------------------------------------------


def _topo_with_source():
    return MoIPTopology(
        zones={11: _zone(paired_tx_id=41)},
        sources={
            41: MoIPSource(group_id=41, name="TX-1", hw_label="HDMI", unit_name="Rack"),
            42: MoIPSource(group_id=42, name="TX-2", hw_label="Analog", unit_name="Rack"),
        },
    )


def test_source_reports_current_label():
    ent, _ = _entity(_topo_with_source())
    assert ent.source == "Rack – HDMI"


def test_source_is_none_when_unpaired():
    topo = _topo_with_source()
    topo.zones[11].paired_tx_id = None
    ent, _ = _entity(topo)
    assert ent.source == SOURCE_NONE


def test_source_list_starts_with_none_then_sorted_enabled():
    ent, _ = _entity(_topo_with_source())
    sl = ent.source_list
    assert sl[0] == SOURCE_NONE
    assert sl == [SOURCE_NONE, "Rack – Analog", "Rack – HDMI"]


def test_source_list_excludes_disabled_sources():
    options = {"sources": {"42": {"enabled": False}}}
    ent, _ = _entity(_topo_with_source(), options=options)
    assert ent.source_list == [SOURCE_NONE, "Rack – HDMI"]


# --- service calls ----------------------------------------------------------


async def test_async_set_volume_level_calls_client_and_refreshes():
    topo = _topo_with_source()
    ent, coord = _entity(topo)
    await ent.async_set_volume_level(0.5)
    coord.client.async_set_volume.assert_awaited_once()
    args = coord.client.async_set_volume.await_args.args
    assert args[0] is topo.zones[11] and args[1] == 0.5
    coord.async_request_refresh.assert_awaited_once()


async def test_async_mute_volume_calls_client():
    topo = _topo_with_source()
    ent, coord = _entity(topo)
    await ent.async_mute_volume(True)
    coord.client.async_set_mute.assert_awaited_once_with(topo.zones[11], True)
    coord.async_request_refresh.assert_awaited_once()


async def test_async_select_source_named_resolves_to_tx_id():
    topo = _topo_with_source()
    ent, coord = _entity(topo)
    await ent.async_select_source("Rack – Analog")
    coord.client.async_select_source.assert_awaited_once_with(11, 42)
    coord.async_request_refresh.assert_awaited_once()


async def test_async_select_source_none_unpairs():
    topo = _topo_with_source()
    ent, coord = _entity(topo)
    await ent.async_select_source(SOURCE_NONE)
    coord.client.async_select_source.assert_awaited_once_with(11, None)


async def test_async_select_source_unknown_raises():
    topo = _topo_with_source()
    ent, coord = _entity(topo)
    with pytest.raises(ValueError):
        await ent.async_select_source("Nonexistent")
    coord.client.async_select_source.assert_not_called()
