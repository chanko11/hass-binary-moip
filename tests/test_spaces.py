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


def test_migrate_anchors_rekeys_old_space_to_label(hass):
    coord = _coordinator(_zone(1))
    mgr = SpaceManager(hass, coord)
    anchors = {"main_living": {"background": {"1032": 30.0}}}
    changed = mgr._migrate_anchors([{"id": "main_living", "label": "common"}], anchors)
    assert changed is True
    assert "main_living" not in anchors
    assert anchors["common"]["background"]["1032"] == 30.0
    # idempotent: a space whose id already equals its label is left alone
    assert mgr._migrate_anchors([{"id": "common", "label": "common"}], anchors) is False


async def test_zones_for_label_resolves_via_area(hass):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import label_registry as lr

    lr.async_get(hass).async_create("Common")  # -> label_id "common"
    areg = ar.async_get(hass)
    kitchen = areg.async_create("Kitchen")
    areg.async_update(kitchen.id, labels={"common"})
    bath = areg.async_create("Bath")  # no label
    ereg = er.async_get(hass)
    z = ereg.async_get_or_create("media_player", "binary_moip", "e1_1032")
    ereg.async_update_entity(z.entity_id, area_id=kitchen.id)
    nb = ereg.async_get_or_create("media_player", "binary_moip", "e1_1099")
    ereg.async_update_entity(nb.entity_id, area_id=bath.id)

    coord = _coordinator(_zone(1032), _zone(1099))  # config_entry.entry_id="e1"
    mgr = SpaceManager(hass, coord)
    assert mgr._zones_for_label("common") == [1032]  # bath zone (unlabeled) excluded


async def test_async_load_builds_spaces_from_labels(hass):
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import label_registry as lr

    lr.async_get(hass).async_create("Common")  # -> label_id "common"
    lr.async_get(hass).async_create("Empty")  # no zones -> not a space
    areg = ar.async_get(hass)
    kitchen = areg.async_create("Kitchen")
    areg.async_update(kitchen.id, labels={"common"})
    ereg = er.async_get(hass)
    z = ereg.async_get_or_create("media_player", "binary_moip", "e1_1032")
    ereg.async_update_entity(z.entity_id, area_id=kitchen.id)

    mgr = SpaceManager(hass, _coordinator(_zone(1032)))
    await mgr.async_load()
    # the "common" label (with a zone) becomes a Space; "empty" (no zone) doesn't
    assert set(mgr.spaces) == {"common"}
    assert mgr.spaces["common"].name == "Common"
    assert mgr.spaces["common"].zones == [1032]


async def test_describe_spaces_snapshot(hass):
    from homeassistant.helpers import entity_registry as er

    ereg = er.async_get(hass)
    z = ereg.async_get_or_create("media_player", "binary_moip", "e1_1032")
    hass.states.async_set(z.entity_id, "idle", {"friendly_name": "Kitchen"})
    zone = _zone(1032)
    zone.volume = 40.0
    mgr = SpaceManager(hass, _coordinator(zone))
    mgr.spaces["s1"] = Space(id="s1", name="Main", zones=[1032], levels={"background": {1032: 30.0}})

    desc = mgr.describe()
    assert len(desc) == 1
    s = desc[0]
    assert s["id"] == "s1" and s["name"] == "Main" and s["active"] is False
    z0 = s["zones"][0]
    assert z0["group_id"] == 1032 and z0["entity_id"] == z.entity_id
    assert z0["name"] == "Kitchen"
    assert z0["anchors"]["background"] == 30.0 and z0["calibrated"] is True
    assert z0["current_native"] == 40.0 and z0["bounds"] == [0.0, 100.0]
    assert z0["online"] is True and s["offline_zones"] == 0 and s["zones_total"] == 1


async def test_zone_online_reflects_amp_and_zone_state(hass):
    from custom_components.binary_moip.api import MoIPUnit

    z_on = MoIPZone(group_id=11, name="On", unit_id=1, volume_range=(0.0, 100.0))
    z_zoff = MoIPZone(group_id=12, name="ZOff", unit_id=1, state="unconnected")
    z_uoff = MoIPZone(group_id=13, name="UOff", unit_id=2)  # amp offline
    coord = _coordinator(z_on, z_zoff, z_uoff)
    coord.data.units = {
        1: MoIPUnit(unit_id=1, name="AmpA", state="operating"),
        2: MoIPUnit(unit_id=2, name="AmpB", state="unconnected"),
    }
    mgr = SpaceManager(hass, coord)
    assert mgr._zone_online(11) is True
    assert mgr._zone_online(12) is False  # zone itself reports unconnected
    assert mgr._zone_online(13) is False  # parent amp is unconnected
    assert mgr._zone_online(99) is False  # not discovered at all


async def test_uncalibrated_zone_tracks_master(hass):
    z1 = _zone(11)  # range 0..100
    coord = _coordinator(z1)
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = Space(id="s1", name="S1", zones=[11], levels={})  # no anchors
    # listening (position 50) -> 50% of the native range, not a fixed default
    await mgr.async_activate("s1", level="listening", source=7)
    coord.client.async_set_volume_native.assert_any_await(z1, 50.0)
    # the master slider still moves an uncalibrated zone (the reported bug)
    await mgr.async_set_master("s1", 85)
    coord.client.async_set_volume_native.assert_any_await(z1, 85.0)
    await mgr.async_set_master("s1", 20)
    coord.client.async_set_volume_native.assert_any_await(z1, 20.0)


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
    assert play[0].data["entity_id"] == "media_player.ma1"  # the source's backing


async def test_calibration_play_music_plays_configured_reference(hass):
    play = async_mock_service(hass, "media_player", "play_media")
    async_mock_service(hass, "media_player", "repeat_set")
    coord = _coordinator(
        _zone(11),
        options={
            "sources": {"7": {"backing_entity": "media_player.ma1"}},
            "calibration": {"music_media": "media-source://local/song.flac", "music_type": "music"},
        },
    )
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])
    # music mode (ref_type sample) with a configured music reference -> plays it
    await mgr.async_calibration_play("s1", ref_type="sample", source=7)
    assert play and play[0].data["media_content_id"] == "media-source://local/song.flac"
    assert play[0].data["entity_id"] == "media_player.ma1"


async def test_calibration_play_set_levels_false_keeps_volumes(hass):
    play = async_mock_service(hass, "media_player", "play_media")
    async_mock_service(hass, "media_player", "repeat_set")
    coord = _coordinator(
        _zone(11),
        options={
            "sources": {"7": {"backing_entity": "media_player.ma1"}},
            "calibration": {"pink_media": "media-source://local/pink.flac", "pink_type": "music"},
        },
    )
    mgr = SpaceManager(hass, coord)
    mgr.spaces["s1"] = _space("s1", [11])
    await mgr.async_activate("s1", level="listening", source=7)  # active, tx_id=7
    coord.client.async_select_source.reset_mock()
    coord.client.async_set_volume_native.reset_mock()

    # swap to pink without re-leveling: no re-route, no volume changes, just audio
    await mgr.async_calibration_play("s1", ref_type="pink", set_levels=False)
    coord.client.async_select_source.assert_not_called()
    coord.client.async_set_volume_native.assert_not_called()
    assert play and play[0].data["entity_id"] == "media_player.ma1"
