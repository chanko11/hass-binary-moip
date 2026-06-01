"""Tests for api.py's pure parsing/normalization helpers (no I/O)."""

from __future__ import annotations

import pytest
from conftest import api


# --- _opt_int ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (0, None),       # association id 0 means "unset"
        ("0", 0),        # string "0" is a real coercion, not the unset sentinel
        (5, 5),
        ("5", 5),
        (5.0, 5),
        ("abc", None),   # non-numeric -> None, not a crash
        ([], None),      # unexpected type -> None
    ],
)
def test_opt_int(value, expected):
    assert api._opt_int(value) == expected


# --- _parse_unit ------------------------------------------------------------


def test_parse_unit_full():
    unit = api._parse_unit(
        {
            "id": 3,
            "settings": {"name": "Main 6 Zone Amp"},
            "status": {"model": "EA-MOIP-AMP-12D-100", "mac": "AA:BB", "unit_state": "online"},
        }
    )
    assert (unit.unit_id, unit.name, unit.model, unit.mac, unit.state) == (
        3,
        "Main 6 Zone Amp",
        "EA-MOIP-AMP-12D-100",
        "AA:BB",
        "online",
    )
    assert unit.raw["id"] == 3


def test_parse_unit_name_fallback_when_unnamed():
    unit = api._parse_unit({"id": 7, "settings": {}, "status": {}})
    assert unit.name == "Unit 7"
    assert unit.model is None and unit.mac is None and unit.state is None


def test_parse_unit_tolerates_missing_settings_and_status():
    unit = api._parse_unit({"id": 9})
    assert unit.name == "Unit 9"


# --- _parse_zone ------------------------------------------------------------


def test_parse_zone_uses_group_rx_name_not_hardware():
    zone = api._parse_zone(
        {
            "id": 11,
            "settings": {"name": "Kitchen"},
            "associations": {"unit": 3, "audio_rx": 21, "paired_tx": 31},
            "status": {"state": "streaming"},
        },
        units={},
    )
    assert zone.name == "Kitchen"
    assert (zone.group_id, zone.unit_id, zone.audio_rx_id, zone.paired_tx_id) == (
        11,
        3,
        21,
        31,
    )
    assert zone.state == "streaming"


def test_parse_zone_name_falls_back_to_unit_name():
    units = {3: api.MoIPUnit(unit_id=3, name="Main Amp")}
    zone = api._parse_zone(
        {"id": 12, "settings": {}, "associations": {"unit": 3}}, units=units
    )
    assert zone.name == "Main Amp zone"


def test_parse_zone_name_falls_back_to_zone_id_when_no_unit():
    zone = api._parse_zone(
        {"id": 13, "settings": {}, "associations": {}}, units={}
    )
    assert zone.name == "Zone 13"


def test_parse_zone_unpaired_tx_is_none():
    zone = api._parse_zone(
        {"id": 14, "settings": {"name": "Den"}, "associations": {"paired_tx": 0}},
        units={},
    )
    assert zone.paired_tx_id is None  # 0 -> unset


# --- _parse_source ----------------------------------------------------------


def test_parse_source_full_with_unit_name():
    units = {5: api.MoIPUnit(unit_id=5, name="AV Rack")}
    source = api._parse_source(
        {
            "id": 41,
            "settings": {"name": "TX-Sonos"},
            "associations": {"unit": 5, "audio_tx": 51},
            "status": {"state": "stopped"},
        },
        units=units,
    )
    assert source.name == "TX-Sonos"
    assert source.unit_name == "AV Rack"
    assert (source.group_id, source.unit_id, source.audio_tx_id) == (41, 5, 51)
    assert source.state == "stopped"


def test_parse_source_name_fallback_and_unknown_unit():
    source = api._parse_source(
        {"id": 42, "settings": {}, "associations": {"unit": 999}}, units={}
    )
    assert source.name == "Source 42"
    assert source.unit_name is None  # unit 999 not in topology


# --- _apply_audio_rx --------------------------------------------------------


def test_apply_audio_rx_populates_volume_range_and_mute_state():
    zone = api.MoIPZone(group_id=1, name="Z")
    api._apply_audio_rx(
        zone,
        {
            "settings": {
                "volume": 40,
                "maxvolume": 80,
                "supported_volume": {"range": [0, 100]},
                "supported_output": ["lineout", "speaker"],
                "mute": ["lineout"],  # non-empty -> muted
            },
            "status": {"state": "streaming"},
        },
    )
    assert zone.volume == 40
    assert zone.max_volume == 80
    assert zone.volume_range == (0.0, 100.0)
    assert zone.mute_ports == ["lineout", "speaker"]
    assert zone.muted is True
    assert zone.state == "streaming"


def test_apply_audio_rx_empty_mute_list_is_unmuted():
    zone = api.MoIPZone(group_id=1, name="Z")
    api._apply_audio_rx(zone, {"settings": {"mute": []}, "status": {}})
    assert zone.muted is False


def test_apply_audio_rx_bad_range_left_unset():
    zone = api.MoIPZone(group_id=1, name="Z")
    api._apply_audio_rx(
        zone, {"settings": {"supported_volume": {"range": [0]}}, "status": {}}
    )
    assert zone.volume_range is None


def test_apply_audio_rx_none_raw_is_noop():
    zone = api.MoIPZone(group_id=1, name="Z", state="kept")
    api._apply_audio_rx(zone, None)
    assert zone.state == "kept" and zone.volume is None


def test_apply_audio_rx_does_not_clobber_state_when_absent():
    zone = api.MoIPZone(group_id=1, name="Z", state="prev")
    api._apply_audio_rx(zone, {"settings": {"volume": 10}, "status": {}})
    assert zone.state == "prev"  # status.state absent -> keep existing


# --- _apply_audio_tx (the firmware quirk) -----------------------------------


def test_apply_audio_tx_normalizes_source_list_to_single_string():
    # Firmware returns settings.source as a list even though the spec types it
    # as a single AudioPort enum.
    source = api.MoIPSource(group_id=1, name="S")
    api._apply_audio_tx(
        source, {"label": "Digital Input", "settings": {"source": ["toslink"]}}
    )
    assert source.hw_label == "Digital Input"
    assert source.input_type == "toslink"


def test_apply_audio_tx_accepts_plain_string_source():
    source = api.MoIPSource(group_id=1, name="S")
    api._apply_audio_tx(source, {"label": "Analog", "settings": {"source": "analog"}})
    assert source.input_type == "analog"


def test_apply_audio_tx_list_skips_empty_entries():
    source = api.MoIPSource(group_id=1, name="S")
    api._apply_audio_tx(source, {"settings": {"source": ["", "hdmi"]}})
    assert source.input_type == "hdmi"


def test_apply_audio_tx_empty_list_yields_none():
    source = api.MoIPSource(group_id=1, name="S")
    api._apply_audio_tx(source, {"settings": {"source": []}})
    assert source.input_type is None


def test_apply_audio_tx_none_raw_is_noop():
    source = api.MoIPSource(group_id=1, name="S", hw_label="keep")
    api._apply_audio_tx(source, None)
    assert source.hw_label == "keep"
