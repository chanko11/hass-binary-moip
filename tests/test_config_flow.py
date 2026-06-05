"""Tests for the config flow (user setup + options flow)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.binary_moip import config_flow as cf
from custom_components.binary_moip.api import (
    BinaryMoIPAuthError,
    BinaryMoIPConnectionError,
    MoIPSource,
    MoIPTopology,
    MoIPZone,
)
from custom_components.binary_moip.const import (
    DOMAIN,
    OPT_ENABLED,
    OPT_LABEL,
    OPT_ZONES,
)

USER_INPUT = {
    "host": "ctrl.local",
    "port": 443,
    "username": "admin",
    "password": "secret",
    "verify_ssl": False,
}


# --- user setup flow --------------------------------------------------------


async def test_user_flow_success_creates_entry(hass, enable_custom_integrations):
    with patch(
        "custom_components.binary_moip.config_flow.BinaryMoIPClient"
    ) as client_cls, patch(
        "custom_components.binary_moip.async_setup_entry", return_value=True
    ):
        client_cls.return_value.authenticate = AsyncMock(return_value=None)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "ctrl.local"
    assert result2["data"] == USER_INPUT


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (BinaryMoIPAuthError("bad creds"), "invalid_auth"),
        (BinaryMoIPConnectionError("unreachable"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_error_paths(hass, enable_custom_integrations, exc, expected_error):
    with patch("custom_components.binary_moip.config_flow.BinaryMoIPClient") as client_cls:
        client_cls.return_value.authenticate = AsyncMock(side_effect=exc)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": expected_error}


async def test_user_flow_aborts_on_duplicate_host(hass, enable_custom_integrations):
    MockConfigEntry(domain=DOMAIN, unique_id="ctrl.local", data=USER_INPUT).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# --- options-flow pure helpers ---------------------------------------------


def test_unique_displays_appends_id_on_collision():
    items = [(11, "Bath"), (12, "Bath"), (13, "Kitchen")]
    assert cf._unique_displays(items) == {
        11: "Bath [11]",
        12: "Bath [12]",
        13: "Kitchen",
    }


def test_build_options_schema_keys_map_to_ids():
    items = [(11, "Kitchen")]
    schema, enabled_keys, label_keys, backing_keys = cf._build_options_schema(items, {})
    assert enabled_keys == {"Kitchen": 11}
    assert label_keys == {"Kitchen — custom name": 11}
    assert backing_keys == {}  # no backing field unless include_backing=True


def test_build_options_schema_includes_backing_for_sources():
    items = [(41, "Record Player")]
    _, _, _, backing_keys = cf._build_options_schema(items, {}, include_backing=True)
    assert backing_keys == {"Record Player — backing media_player": 41}


def test_parse_options_stores_backing_entity():
    backing_keys = {"Record Player — backing media_player": 41}
    user_input = {"Record Player — backing media_player": "media_player.streaming_1"}
    result = cf._parse_options(user_input, {}, {}, backing_keys)
    assert result == {"41": {"backing_entity": "media_player.streaming_1"}}


def test_parse_options_stores_only_non_defaults():
    enabled_keys = {"Kitchen": 11, "Den": 12}
    label_keys = {"Kitchen — custom name": 11, "Den — custom name": 12}
    user_input = {
        "Kitchen": False,                 # disabled -> stored
        "Den": True,                      # default -> omitted
        "Kitchen — custom name": "   ",   # blank -> omitted
        "Den — custom name": "Cave",      # label -> stored
    }
    result = cf._parse_options(user_input, enabled_keys, label_keys)
    assert result == {"11": {OPT_ENABLED: False}, "12": {OPT_LABEL: "Cave"}}


def test_parse_options_untouched_yields_empty():
    enabled_keys = {"Kitchen": 11}
    label_keys = {"Kitchen — custom name": 11}
    assert cf._parse_options({"Kitchen": True, "Kitchen — custom name": ""}, enabled_keys, label_keys) == {}


# --- options-flow integration ----------------------------------------------


async def test_options_flow_zones_step_saves_selection(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    # The zones step reads discovered zones off the coordinator in runtime_data.
    entry.runtime_data = SimpleNamespace(
        data=MoIPTopology(
            zones={11: MoIPZone(group_id=11, name="Kitchen")},
            sources={41: MoIPSource(group_id=41, name="TX-1")},
        )
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "zones"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "zones"

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"], {"Kitchen": False}  # disable the Kitchen zone
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][OPT_ZONES] == {"11": {OPT_ENABLED: False}}


async def test_options_flow_sources_step_saves_label(hass, enable_custom_integrations):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        data=MoIPTopology(
            zones={11: MoIPZone(group_id=11, name="Kitchen")},
            sources={
                41: MoIPSource(group_id=41, name="TX-1", hw_label="HDMI", unit_name="Rack")
            },
        )
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "sources"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "sources"

    # The synthesized source display label is "Rack – HDMI"; give it a custom name.
    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"], {"Rack – HDMI — custom name": "Apple TV"}
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"]["sources"] == {"41": {OPT_LABEL: "Apple TV"}}
