"""Tests for the zone driver abstraction (spec §3/§11)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.binary_moip import drivers as D
from custom_components.binary_moip.api import MoIPTopology, MoIPZone


def _coordinator(zone: MoIPZone) -> MagicMock:
    coord = MagicMock()
    coord.data = MoIPTopology(zones={zone.group_id: zone})
    coord.client = MagicMock()
    coord.client.async_set_volume_native = AsyncMock()
    return coord


async def test_moip_driver_sets_native_volume_and_bounds():
    zone = MoIPZone(
        group_id=11, name="Kitchen", audio_rx_id=5,
        volume=20, volume_range=(0.0, 100.0), max_volume=90.0,
    )
    coord = _coordinator(zone)
    driver = D.MoipDriver(coord, 11)
    assert driver.bounds == (0.0, 90.0)  # capped by max_volume
    await driver.activate()  # no-op (routing owned by manager)
    await driver.set_level(55.0)
    coord.client.async_set_volume_native.assert_awaited_once_with(zone, 55.0)


async def test_build_driver_defaults_to_moip():
    zone = MoIPZone(group_id=11, name="K", audio_rx_id=5, volume_range=(0.0, 100.0))
    coord = _coordinator(zone)
    assert isinstance(D.build_driver(MagicMock(), coord, 11, None), D.MoipDriver)
    assert isinstance(
        D.build_driver(MagicMock(), coord, 11, {"type": "moip"}), D.MoipDriver
    )


async def test_device_driver_activate_set_off_when_we_powered_it(hass):
    turn_on = async_mock_service(hass, "media_player", "turn_on")
    select = async_mock_service(hass, "media_player", "select_source")
    vol = async_mock_service(hass, "media_player", "volume_set")
    turn_off = async_mock_service(hass, "media_player", "turn_off")
    hass.states.async_set("media_player.avr", "off")

    driver = D.build_driver(
        hass, MagicMock(), 99,
        {"type": "device", "entity_id": "media_player.avr", "input": "CD", "min": 0.0, "max": 1.0},
    )
    assert isinstance(driver, D.DeviceDriver)
    assert driver.bounds == (0.0, 1.0)

    await driver.activate()
    assert len(turn_on) == 1
    assert len(select) == 1 and select[0].data["source"] == "CD"

    await driver.set_level(0.5)
    assert len(vol) == 1 and vol[0].data["volume_level"] == 0.5

    await driver.deactivate()  # we powered it on -> power it off
    assert len(turn_off) == 1


async def test_device_driver_leaves_externally_powered_device_on(hass):
    turn_on = async_mock_service(hass, "media_player", "turn_on")
    turn_off = async_mock_service(hass, "media_player", "turn_off")
    async_mock_service(hass, "media_player", "select_source")
    hass.states.async_set("media_player.avr", "on")  # already on (TV using it)

    driver = D.DeviceDriver(hass, {"entity_id": "media_player.avr", "input": "CD"})
    await driver.activate()
    assert len(turn_on) == 0  # didn't power it on
    await driver.deactivate()
    assert len(turn_off) == 0  # so don't power it off
