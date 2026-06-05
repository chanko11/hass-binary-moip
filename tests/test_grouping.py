"""Grouping (join/unjoin/group_members) for source + zone media_players.

Full-HA integration tests: set up the entry with a mocked client, then drive the
media_player.join / media_player.unjoin services and assert (a) the existing
"set zone source" primitive is called with the right ids and (b) group_members
reflects controller routing live, updating on coordinator pushes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.binary_moip.api import (
    MoIPSource,
    MoIPTopology,
    MoIPUnit,
    MoIPZone,
)
from custom_components.binary_moip.const import DOMAIN

USER_INPUT = {"host": "ctrl.local", "port": 443, "username": "u", "password": "p", "verify_ssl": False}

# group_rx 11 = Living Room (routed to source 41), 12 = Kitchen (unpaired).
# group_tx 41 = Record Player (a standalone transmitter).
ZONE_LR, ZONE_KIT, SRC = 11, 12, 41


def _topology() -> MoIPTopology:
    return MoIPTopology(
        units={
            3: MoIPUnit(unit_id=3, name="Main Amp", model="EA-MOIP-AMP-12D-100"),
            9: MoIPUnit(unit_id=9, name="Record Player Transmitter", model="B-900-MOIP-A-TX"),
        },
        zones={
            ZONE_LR: MoIPZone(group_id=ZONE_LR, name="Living Room", unit_id=3, audio_rx_id=21, paired_tx_id=SRC, state="streaming"),
            ZONE_KIT: MoIPZone(group_id=ZONE_KIT, name="Kitchen", unit_id=3, audio_rx_id=22, paired_tx_id=None, state="stopped"),
        },
        sources={
            SRC: MoIPSource(group_id=SRC, name="Record Player", unit_id=9, hw_label="Audio Input", input_type="analog"),
        },
    )


async def _setup(hass):
    """Set up the entry with a fully mocked client; return (entry, client, eids)."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="ctrl.local")
    entry.add_to_hass(hass)

    client = MagicMock()
    client.authenticate = AsyncMock()
    client.async_discover = AsyncMock(return_value=_topology())
    client.async_select_source = AsyncMock()
    parked = asyncio.Event()  # ws never connects -> background listener parks

    async def _never_connects():
        await parked.wait()

    client.async_ws_connect = AsyncMock(side_effect=_never_connects)

    with patch("custom_components.binary_moip.BinaryMoIPClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reg = er.async_get(hass)
    eids = {
        "source": reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_source_{SRC}"),
        "lr": reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_{ZONE_LR}"),
        "kit": reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_{ZONE_KIT}"),
    }
    return entry, client, eids


def _members(hass, eid):
    return hass.states.get(eid).attributes.get("group_members")


async def _join(hass, leader, members):
    await hass.services.async_call(
        "media_player", "join", {"entity_id": leader, "group_members": members}, blocking=True
    )


async def _unjoin(hass, entity_id):
    await hass.services.async_call("media_player", "unjoin", {"entity_id": entity_id}, blocking=True)


# --- entities exist ---------------------------------------------------------


async def test_source_entity_created(hass, enable_custom_integrations):
    _, _, eids = await _setup(hass)
    assert eids["source"] == "media_player.record_player"
    assert hass.states.get(eids["source"]) is not None


# --- group_members derivation ----------------------------------------------


async def test_group_members_leader_first_and_consistent(hass, enable_custom_integrations):
    _, _, eids = await _setup(hass)
    # Source leads; Living Room is routed to it; Kitchen is its own lone group.
    expected_group = [eids["source"], eids["lr"]]
    assert _members(hass, eids["source"]) == expected_group
    assert _members(hass, eids["lr"]) == expected_group       # member reports same list
    assert _members(hass, eids["kit"]) == [eids["kit"]]       # unpaired -> alone


# --- join (source-first) ----------------------------------------------------


async def test_join_on_source_routes_each_zone(hass, enable_custom_integrations):
    _, client, eids = await _setup(hass)
    await _join(hass, eids["source"], [eids["kit"]])
    # Routed via the existing primitive: Kitchen (group_rx 12) -> source (group_tx 41).
    client.async_select_source.assert_any_await(ZONE_KIT, SRC)


async def test_join_on_zone_adds_to_its_current_source(hass, enable_custom_integrations):
    _, client, eids = await _setup(hass)
    # Living Room is on the source; joining Kitchen to Living Room routes Kitchen
    # to Living Room's source.
    await _join(hass, eids["lr"], [eids["kit"]])
    client.async_select_source.assert_any_await(ZONE_KIT, SRC)


async def test_join_on_unpaired_zone_raises(hass, enable_custom_integrations):
    _, client, eids = await _setup(hass)
    with pytest.raises(HomeAssistantError):
        await _join(hass, eids["kit"], [eids["lr"]])  # Kitchen has no source
    client.async_select_source.assert_not_awaited()


# --- unjoin -----------------------------------------------------------------


async def test_unjoin_on_zone_unpairs_only_that_zone(hass, enable_custom_integrations):
    _, client, eids = await _setup(hass)
    await _unjoin(hass, eids["lr"])
    client.async_select_source.assert_awaited_once_with(ZONE_LR, None)


async def test_unjoin_on_source_disbands_all_routed_zones(hass, enable_custom_integrations):
    _, client, eids = await _setup(hass)
    await _unjoin(hass, eids["source"])
    # Only Living Room is routed to the source, so only it gets unpaired.
    client.async_select_source.assert_awaited_once_with(ZONE_LR, None)


# --- real-time update on controller routing change --------------------------


async def test_zone_routed_to_disabled_source_is_lone_group(hass, enable_custom_integrations):
    # Disable the source entity, but the controller still has Living Room routed
    # to it. With no source entity to lead, the zone reports a lone group.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="ctrl.local",
        options={"sources": {str(SRC): {"enabled": False}}},
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
    assert reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_source_{SRC}") is None
    lr_eid = reg.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_{ZONE_LR}")
    assert _members(hass, lr_eid) == [lr_eid]


async def test_group_members_updates_on_coordinator_push(hass, enable_custom_integrations):
    entry, _, eids = await _setup(hass)
    coordinator = entry.runtime_data

    # Simulate the controller routing Kitchen to the source too (e.g. via the
    # websocket); push it through the coordinator with no polling.
    coordinator.data.zones[ZONE_KIT].paired_tx_id = SRC
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    # Members sorted by entity_id: kitchen < living_room.
    assert _members(hass, eids["source"]) == [eids["source"], eids["kit"], eids["lr"]]
    assert _members(hass, eids["kit"]) == [eids["source"], eids["kit"], eids["lr"]]
