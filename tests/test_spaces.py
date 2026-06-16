"""Tests for the Listening Space manager (spec §4–§6, §11)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.binary_moip.api import MoIPTopology, MoIPZone
from custom_components.binary_moip.spaces import Space, SpaceManager


def _coordinator(*zones: MoIPZone, options: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = MoIPTopology(zones={z.group_id: z for z in zones})
    coord.client = MagicMock()
    coord.client.async_select_source = AsyncMock()
    coord.client.async_set_volume_native = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.config_entry = SimpleNamespace(entry_id="e1", options=options or {})
    return coord


def _zone(gid: int) -> MoIPZone:
    return MoIPZone(group_id=gid, name=f"Z{gid}", audio_rx_id=gid, volume_range=(0.0, 100.0))


def _space(sid: str, zones: list[int]) -> Space:
    return Space(
        id=sid,
        name=sid,
        zones=zones,
        levels={
            "background": {g: 10 for g in zones},
            "listening": {g: 40 for g in zones},
            "party": {g: 80 for g in zones},
        },
    )


def test_space_roundtrip_dict():
    s = _space("s1", [11, 12])
    s2 = Space.from_dict(s.to_dict())
    assert s2.zones == [11, 12]
    assert s2.levels["listening"] == {11: 40.0, 12: 40.0}
    assert s2.master_positions["party"] == 85.0


async def test_activate_routes_and_applies_listening_level(hass):
    z1, z2 = _zone(11), _zone(12)
    coord = _coordinator(z1, z2)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11, 12])

    await mgr.async_activate("s1", level="listening", source=7)  # int source -> tx 7

    assert coord.client.async_select_source.await_count == 2
    coord.client.async_select_source.assert_any_await(11, 7)
    coord.client.async_select_source.assert_any_await(12, 7)
    # listening position (50) -> exact listening anchor value (40) per zone
    coord.client.async_set_volume_native.assert_any_await(z1, 40.0)
    coord.client.async_set_volume_native.assert_any_await(z2, 40.0)
    assert mgr.zone_owner == {11: "s1", 12: "s1"}
    assert mgr.active["s1"].level == "listening"


async def test_set_master_reinterpolates(hass):
    z1 = _zone(11)
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])
    await mgr.async_activate("s1", level="background", source=7)
    coord.client.async_set_volume_native.reset_mock()

    await mgr.async_set_master("s1", 85)  # party position -> party value 80
    coord.client.async_set_volume_native.assert_awaited_once_with(z1, 80.0)
    assert mgr.active["s1"].level == "party"


async def test_overlap_disallowed_steals_zone(hass):
    z1, z2, z3 = _zone(11), _zone(12), _zone(13)
    coord = _coordinator(z1, z2, z3)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["a"] = _space("a", [11, 12])
    mgr.spaces["b"] = _space("b", [12, 13])  # shares zone 12

    await mgr.async_activate("a", level="listening", source=7)
    await mgr.async_activate("b", level="party", source=8)

    # zone 12 was stolen by b; a keeps only 11
    assert mgr.zone_owner == {11: "a", 12: "b", 13: "b"}

    # moving a's master must NOT touch the stolen zone 12
    coord.client.async_set_volume_native.reset_mock()
    await mgr.async_set_master("a", 85)
    awaited_zones = {c.args[0].group_id for c in coord.client.async_set_volume_native.await_args_list}
    assert awaited_zones == {11}


async def test_set_anchor_explicit_value_and_clear(hass):
    z1 = _zone(11)
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = Space(id="s1", name="S1", zones=[11], levels={})

    stored = await mgr.async_set_anchor("s1", 11, "background", value=18.0)
    assert stored == 18.0
    assert mgr.spaces["s1"].levels["background"][11] == 18.0

    await mgr.async_clear_anchor("s1", 11, "background")
    assert 11 not in mgr.spaces["s1"].levels.get("background", {})


async def test_set_anchor_snapshots_current_native(hass):
    z1 = _zone(11)
    z1.volume = 27.0  # current live native volume
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = Space(id="s1", name="S1", zones=[11], levels={})

    stored = await mgr.async_set_anchor("s1", 11, "party")  # no value -> snapshot
    assert stored == 27.0
    assert mgr.spaces["s1"].levels["party"][11] == 27.0


async def test_set_anchor_rejects_zone_not_in_space(hass):
    coord = _coordinator(_zone(11))
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = Space(id="s1", name="S1", zones=[11], levels={})
    import pytest

    with pytest.raises(ValueError):
        await mgr.async_set_anchor("s1", 99, "listening", value=10)


async def test_deactivate_unroutes_and_releases(hass):
    z1 = _zone(11)
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])
    await mgr.async_activate("s1", level="listening", source=7)

    await mgr.async_deactivate("s1")
    coord.client.async_select_source.assert_any_await(11, None)  # unpaired
    assert "s1" not in mgr.active
    assert 11 not in mgr.zone_owner


async def test_uncalibrated_zone_gets_safe_default_not_full(hass):
    z1 = _zone(11)  # range 0..100
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = Space(id="s1", name="S1", zones=[11], levels={})  # no anchors
    await mgr.async_activate("s1", level="listening", source=7)
    # safe default = 0.3 * (100-0) = 30, never blasted to full
    coord.client.async_set_volume_native.assert_any_await(z1, 30.0)


async def test_zone_set_off_then_on(hass):
    z1, z2 = _zone(11), _zone(12)
    coord = _coordinator(z1, z2)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11, 12])
    await mgr.async_activate("s1", level="listening", source=7)

    await mgr.async_zone_set("s1", 12, "off")
    coord.client.async_select_source.assert_any_await(12, None)
    assert 12 not in mgr.zone_owner

    coord.client.async_select_source.reset_mock()
    coord.client.async_set_volume_native.reset_mock()
    await mgr.async_zone_set("s1", 12, "on")
    coord.client.async_select_source.assert_any_await(12, 7)  # re-routed to active tx
    coord.client.async_set_volume_native.assert_any_await(z2, 40.0)  # listening anchor
    assert mgr.zone_owner[12] == "s1"


async def test_zone_set_nudge(hass):
    z1 = _zone(11)
    z1.volume = 20.0  # current live native
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])
    await mgr.async_activate("s1", level="listening", source=7)
    coord.client.async_set_volume_native.reset_mock()

    await mgr.async_zone_set("s1", 11, "nudge", delta=7.0)
    coord.client.async_set_volume_native.assert_awaited_once_with(z1, 27.0)


async def test_calibration_play_sample_mode_routes_and_plays(hass):
    play = async_mock_service(hass, "media_player", "play_media")
    z1, z2 = _zone(11), _zone(12)
    coord = _coordinator(
        z1, z2, options={"sources": {"7": {"backing_entity": "media_player.ma1"}}}
    )
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11, 12])

    await mgr.async_calibration_play(
        "s1", ref_type="sample", source=7, sample="media-source://x/sample.flac"
    )
    # routed both zones to the source
    coord.client.async_select_source.assert_any_await(11, 7)
    # played the sample on the source's backing player
    assert len(play) == 1
    assert play[0].data["entity_id"] == "media_player.ma1"
    assert play[0].data["media_content_id"] == "media-source://x/sample.flac"


async def test_calibration_play_pink_mode_loops(hass):
    play = async_mock_service(hass, "media_player", "play_media")
    repeat = async_mock_service(hass, "media_player", "repeat_set")
    z1 = _zone(11)
    coord = _coordinator(
        z1,
        options={
            "sources": {"7": {"backing_entity": "media_player.ma1"}},
            "calibration": {
                "pink_player": "media_player.ma1",
                "pink_media": "media-source://local/pink.flac",
                "pink_type": "music",
            },
        },
    )
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])

    await mgr.async_calibration_play("s1", ref_type="pink")
    coord.client.async_select_source.assert_any_await(11, 7)  # source backed by ma1
    assert len(repeat) == 1 and repeat[0].data["repeat"] == "all"
    assert len(play) == 1
    assert play[0].data["media_content_id"] == "media-source://local/pink.flac"
