"""Home Assistant services for Listening Spaces (spec §6, playback subset).

Phase 1 exposes the playback API: activate / deactivate / set_master / set_level.
Calibration services (Phase 2) are added later.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    LEVELS,
    MASTER_MAX,
    MASTER_MIN,
    REF_AUTO,
    REF_PINK,
    REF_SAMPLE,
    ZONE_ACTIONS,
)
from .spaces import SpaceManager

SERVICE_SPACE_ACTIVATE = "space_activate"
SERVICE_SPACE_DEACTIVATE = "space_deactivate"
SERVICE_SPACE_SET_MASTER = "space_set_master"
SERVICE_SPACE_SET_LEVEL = "space_set_level"
SERVICE_ZONE_SET = "zone_set"
SERVICE_CALIBRATION_SET_ANCHOR = "calibration_set_anchor"
SERVICE_CALIBRATION_CLEAR_ANCHOR = "calibration_clear_anchor"
SERVICE_CALIBRATION_PLAY = "calibration_play"

_SPACE = vol.Required("space")

ACTIVATE_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Exclusive("level", "target"): vol.In(LEVELS),
        vol.Exclusive("master", "target"): vol.All(
            vol.Coerce(float), vol.Range(min=MASTER_MIN, max=MASTER_MAX)
        ),
        vol.Optional("source"): cv.string,
    }
)
DEACTIVATE_SCHEMA = vol.Schema({_SPACE: cv.string})
SET_MASTER_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Required("master"): vol.All(
            vol.Coerce(float), vol.Range(min=MASTER_MIN, max=MASTER_MAX)
        ),
    }
)
SET_LEVEL_SCHEMA = vol.Schema({_SPACE: cv.string, vol.Required("level"): vol.In(LEVELS)})
SET_ANCHOR_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Required("zone"): vol.Coerce(int),
        vol.Required("level"): vol.In(LEVELS),
        vol.Optional("value"): vol.Coerce(float),
    }
)
CLEAR_ANCHOR_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Required("zone"): vol.Coerce(int),
        vol.Required("level"): vol.In(LEVELS),
    }
)
ZONE_SET_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Required("zone"): vol.Coerce(int),
        vol.Required("action"): vol.In(ZONE_ACTIONS),
        vol.Optional("delta"): vol.Coerce(float),
    }
)
CALIBRATION_PLAY_SCHEMA = vol.Schema(
    {
        _SPACE: cv.string,
        vol.Optional("ref_type", default=REF_AUTO): vol.In(
            (REF_AUTO, REF_PINK, REF_SAMPLE)
        ),
        vol.Optional("source"): cv.string,
        vol.Optional("sample"): cv.string,
        vol.Optional("level"): vol.In(LEVELS),
        vol.Optional("set_levels", default=True): cv.boolean,
    }
)

_SERVICES = (
    SERVICE_SPACE_ACTIVATE,
    SERVICE_SPACE_DEACTIVATE,
    SERVICE_SPACE_SET_MASTER,
    SERVICE_SPACE_SET_LEVEL,
    SERVICE_ZONE_SET,
    SERVICE_CALIBRATION_SET_ANCHOR,
    SERVICE_CALIBRATION_CLEAR_ANCHOR,
    SERVICE_CALIBRATION_PLAY,
)


def _manager(hass: HomeAssistant) -> SpaceManager:
    """Find the SpaceManager from the (single) loaded binary_moip entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        manager = getattr(coordinator, "space_manager", None)
        if manager is not None:
            return manager
    raise HomeAssistantError("Binary MoIP is not set up")


def async_register_services(hass: HomeAssistant) -> None:
    """Register the Listening Space services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_SPACE_ACTIVATE):
        return

    async def _activate(call: ServiceCall) -> None:
        await _manager(hass).async_activate(
            call.data["space"],
            level=call.data.get("level"),
            master=call.data.get("master"),
            source=call.data.get("source"),
        )

    async def _deactivate(call: ServiceCall) -> None:
        await _manager(hass).async_deactivate(call.data["space"])

    async def _set_master(call: ServiceCall) -> None:
        await _manager(hass).async_set_master(call.data["space"], call.data["master"])

    async def _set_level(call: ServiceCall) -> None:
        await _manager(hass).async_set_level(call.data["space"], call.data["level"])

    async def _set_anchor(call: ServiceCall) -> None:
        await _manager(hass).async_set_anchor(
            call.data["space"], call.data["zone"], call.data["level"], call.data.get("value")
        )

    async def _clear_anchor(call: ServiceCall) -> None:
        await _manager(hass).async_clear_anchor(
            call.data["space"], call.data["zone"], call.data["level"]
        )

    async def _zone_set(call: ServiceCall) -> None:
        await _manager(hass).async_zone_set(
            call.data["space"], call.data["zone"], call.data["action"], call.data.get("delta")
        )

    async def _calibration_play(call: ServiceCall) -> None:
        await _manager(hass).async_calibration_play(
            call.data["space"],
            ref_type=call.data["ref_type"],
            source=call.data.get("source"),
            sample=call.data.get("sample"),
            level=call.data.get("level"),
            set_levels=call.data["set_levels"],
        )

    hass.services.async_register(DOMAIN, SERVICE_SPACE_ACTIVATE, _activate, schema=ACTIVATE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SPACE_DEACTIVATE, _deactivate, schema=DEACTIVATE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SPACE_SET_MASTER, _set_master, schema=SET_MASTER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SPACE_SET_LEVEL, _set_level, schema=SET_LEVEL_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CALIBRATION_SET_ANCHOR, _set_anchor, schema=SET_ANCHOR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CALIBRATION_CLEAR_ANCHOR, _clear_anchor, schema=CLEAR_ANCHOR_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_ZONE_SET, _zone_set, schema=ZONE_SET_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CALIBRATION_PLAY, _calibration_play, schema=CALIBRATION_PLAY_SCHEMA
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the services (called when the last entry unloads)."""
    for service in _SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
