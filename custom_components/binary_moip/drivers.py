"""Zone driver abstraction for Listening Spaces (spec §3).

*Routing* is always MoIP and handled by the SpaceManager (paired_tx). A
**driver** realizes power / input / volume for one zone:

- ``moip``  — set the zone's volume on the MoIP controller (native units).
- ``device`` — delegate to a downstream ``media_player`` (e.g. the theater AVR):
  ``turn_on`` → ``select_source`` → ``volume_set``; smart power-off.

The interpolation layer never knows which driver a zone uses — it computes a
per-zone native value and calls :meth:`ZoneDriver.set_level`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_VOLUME_LEVEL,
    DOMAIN as MP_DOMAIN,
    SERVICE_SELECT_SOURCE,
    SERVICE_VOLUME_SET,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant

from .const import DRIVER_DEVICE, DRIVER_MOIP
from .coordinator import BinaryMoIPDataUpdateCoordinator


class ZoneDriver(ABC):
    """How a single zone's power/input/volume are realized."""

    type: str

    @abstractmethod
    async def activate(self) -> None:
        """Make the zone ready to play (power/input as needed)."""

    @abstractmethod
    async def set_level(self, value: float) -> None:
        """Set the zone's volume in its native units."""

    @abstractmethod
    async def deactivate(self) -> None:
        """Stop/power down the zone (smartly, for shared devices)."""

    @property
    @abstractmethod
    def bounds(self) -> tuple[float | None, float | None]:
        """Native (min, max) for clamping interpolation results."""

    @abstractmethod
    def current_native(self) -> float | None:
        """The zone's current live volume in native units (for snapshotting an
        anchor during calibration). None if unknown.
        """


class MoipDriver(ZoneDriver):
    """Volume on the MoIP controller (current behavior). Routing/power are the
    MoIP route itself, owned by the manager — so activate/deactivate are no-ops.
    """

    type = DRIVER_MOIP

    def __init__(
        self, coordinator: BinaryMoIPDataUpdateCoordinator, group_id: int
    ) -> None:
        self._coordinator = coordinator
        self._group_id = group_id

    @property
    def _zone(self):
        return self._coordinator.data.zones.get(self._group_id)

    async def activate(self) -> None:  # routing handled by the manager
        return None

    async def deactivate(self) -> None:  # unrouting handled by the manager
        return None

    async def set_level(self, value: float) -> None:
        zone = self._zone
        if zone is None:
            return
        await self._coordinator.client.async_set_volume_native(zone, value)

    @property
    def bounds(self) -> tuple[float | None, float | None]:
        zone = self._zone
        if zone is None or zone.volume_range is None:
            return (None, None)
        lo, hi = zone.volume_range
        if zone.max_volume is not None:
            hi = min(hi, zone.max_volume)
        return (lo, hi)

    def current_native(self) -> float | None:
        zone = self._zone
        return None if zone is None else zone.volume


class DeviceDriver(ZoneDriver):
    """Delegate power/input/volume to a downstream ``media_player`` (e.g. AVR).

    ``config``: ``entity_id`` (required), ``input`` (source to select), optional
    ``volume_entity`` (if volume lives on a different entity), ``min``/``max``
    native bounds (default 0..1, the media_player volume scale).
    """

    type = DRIVER_DEVICE

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        self._hass = hass
        self._entity_id: str = config["entity_id"]
        self._input: str | None = config.get("input")
        self._volume_entity: str = config.get("volume_entity") or self._entity_id
        self._min: float = float(config.get("min", 0.0))
        self._max: float = float(config.get("max", 1.0))
        # Whether *we* powered the device on (for smart power-off, spec §12).
        self._powered_by_us = False

    def _is_off(self) -> bool:
        st = self._hass.states.get(self._entity_id)
        return st is None or st.state in (STATE_OFF, STATE_UNAVAILABLE, "standby")

    async def activate(self) -> None:
        if self._is_off():
            await self._hass.services.async_call(
                MP_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: self._entity_id}, blocking=True
            )
            self._powered_by_us = True
        if self._input:
            await self._hass.services.async_call(
                MP_DOMAIN,
                SERVICE_SELECT_SOURCE,
                {ATTR_ENTITY_ID: self._entity_id, ATTR_INPUT_SOURCE: self._input},
                blocking=True,
            )

    async def set_level(self, value: float) -> None:
        await self._hass.services.async_call(
            MP_DOMAIN,
            SERVICE_VOLUME_SET,
            {ATTR_ENTITY_ID: self._volume_entity, ATTR_MEDIA_VOLUME_LEVEL: max(0.0, min(1.0, value))},
            blocking=True,
        )

    async def deactivate(self) -> None:
        # Smart off: only power down if we powered it on. The "other consumer"
        # check (e.g. the theater TV is using the AVR) is spec §12 TBD — for now
        # we only avoid powering down a device we didn't turn on.
        if self._powered_by_us:
            await self._hass.services.async_call(
                MP_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self._entity_id}, blocking=True
            )
            self._powered_by_us = False

    @property
    def bounds(self) -> tuple[float | None, float | None]:
        return (self._min, self._max)

    def current_native(self) -> float | None:
        st = self._hass.states.get(self._volume_entity)
        if st is None:
            return None
        level = st.attributes.get("volume_level")
        return None if level is None else float(level)


def build_driver(
    hass: HomeAssistant,
    coordinator: BinaryMoIPDataUpdateCoordinator,
    group_id: int,
    config: dict | None,
) -> ZoneDriver:
    """Build the driver for a zone from its (optional) driver config.

    No config, or ``type: moip`` -> :class:`MoipDriver`. ``type: device`` ->
    :class:`DeviceDriver`.
    """
    if config and config.get("type") == DRIVER_DEVICE:
        return DeviceDriver(hass, config)
    return MoipDriver(coordinator, group_id)
