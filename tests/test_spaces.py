"""Tests for the Listening Space manager (spec §4–§6, §11)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from custom_components.binary_moip.api import MoIPTopology, MoIPZone
from custom_components.binary_moip.spaces import Space, SpaceManager


def _coordinator(*zones: MoIPZone) -> MagicMock:
    coord = MagicMock()
    coord.data = MoIPTopology(zones={z.group_id: z for z in zones})
    coord.client = MagicMock()
    coord.client.async_select_source = AsyncMock()
    coord.client.async_set_volume_native = AsyncMock()
    coord.async_request_refresh = AsyncMock()
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
