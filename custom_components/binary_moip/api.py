"""API client for the Binary MoIP controller.

Wraps the Binary MoIP REST API v1.3.0:
https://help.snapone.com/moip-ig/Content/Binary%20MoIP%20Topics/API%20v1.3.0.html

Design notes (not yet implemented):
- JWT auth with token refresh. ``authenticate`` obtains an access token;
  requests transparently re-authenticate / refresh on 401.
- The API separates *logical zones* (``group_rx`` — user-named, the unit a
  human thinks of as "the Kitchen") from *hardware outputs* (``audio_rx`` —
  default names tied to physical RX hardware). This client MUST surface zones
  by walking ``group_rx`` so entities get the user's zone names, not the
  hardware defaults. See ``async_get_zones``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


class BinaryMoIPError(Exception):
    """Base error for all Binary MoIP client failures."""


class BinaryMoIPConnectionError(BinaryMoIPError):
    """Raised when the controller cannot be reached."""


class BinaryMoIPAuthError(BinaryMoIPError):
    """Raised when authentication fails or the token cannot be refreshed."""


@dataclass
class MoIPUnit:
    """A physical hardware unit (amp, TX, RX)."""

    unit_id: int
    name: str  # unit.settings.name, e.g. "Main 6 Zone Amp"
    model: str | None = None
    mac: str | None = None
    state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoIPZone:
    """A logical zone derived from a ``group_rx`` entry.

    This is the unit that becomes a Home Assistant media_player entity. ``name``
    is ``group_rx.settings.name`` (the user-editable zone name) — NOT the
    hardware ``audio_rx.label``.
    """

    group_id: int
    name: str
    unit_id: int | None = None
    # The hardware audio_rx output backing this zone (volume/mute/state live here).
    audio_rx_id: int | None = None
    # group_tx id currently routed to this zone (associations.paired_tx); None = off.
    paired_tx_id: int | None = None
    # Live state, populated from the backing audio_rx.
    state: str | None = None
    volume: float | None = None
    max_volume: float | None = None
    volume_range: tuple[float, float] | None = None
    muted: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoIPSource:
    """A logical source derived from a ``group_tx`` entry.

    Becomes a selectable source on zone media_player entities. ``name`` is
    ``group_tx.settings.name`` (often a controller default like
    ``TX-...``); ``hw_label`` and ``input_type`` provide disambiguating info
    for synthesizing a friendly label when the name is non-unique.
    """

    group_id: int
    name: str
    unit_id: int | None = None
    unit_name: str | None = None
    audio_tx_id: int | None = None
    hw_label: str | None = None  # audio_tx.label, e.g. "Digital Input"
    input_type: str | None = None  # e.g. "toslink", "analog", "hdmi"
    state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoIPTopology:
    """The full discovered topology returned by :meth:`async_discover`."""

    units: dict[int, MoIPUnit] = field(default_factory=dict)
    zones: dict[int, MoIPZone] = field(default_factory=dict)
    sources: dict[int, MoIPSource] = field(default_factory=dict)


class BinaryMoIPClient:
    """Async REST client for a single Binary MoIP controller."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        *,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        """Initialize the client. Does not perform any I/O."""
        self._session = session
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl

        self._access_token: str | None = None
        self._refresh_token: str | None = None

    @property
    def base_url(self) -> str:
        """Return the base URL for REST requests."""
        return f"https://{self._host}:{self._port}"

    async def authenticate(self) -> None:
        """Obtain a JWT access/refresh token pair.

        Raises:
            BinaryMoIPAuthError: credentials rejected.
            BinaryMoIPConnectionError: controller unreachable.
        """
        raise NotImplementedError

    async def async_refresh_token(self) -> None:
        """Refresh the access token using the stored refresh token."""
        raise NotImplementedError

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated request, refreshing the token on 401."""
        raise NotImplementedError

    async def async_discover(self) -> MoIPTopology:
        """Discover the full topology: units, zones (group_rx), sources (group_tx).

        Canonical naming source for the integration:
        - zones from ``group_rx.settings.name`` (NOT ``audio_rx.label``),
        - sources from ``group_tx.settings.name``,
        dereferencing ``associations`` to link zones↔audio_rx↔paired source.

        See docs/naming-and-discovery.md for the full graph.
        """
        raise NotImplementedError

    async def async_get_units(self) -> list[MoIPUnit]:
        """List physical units (GET /moip/unit)."""
        raise NotImplementedError

    async def async_get_zones(self) -> list[MoIPZone]:
        """List logical zones by walking ``group_rx`` and their backing audio_rx."""
        raise NotImplementedError

    async def async_get_sources(self) -> list[MoIPSource]:
        """List logical sources by walking ``group_tx``."""
        raise NotImplementedError

    async def async_set_volume(self, zone: MoIPZone, volume: float) -> None:
        """Set volume for a zone. ``volume`` is HA-scale 0.0–1.0.

        Implementation scales into the zone's ``volume_range``/``max_volume``
        and PUTs ``settings.volume`` on the backing ``audio_rx``.
        """
        raise NotImplementedError

    async def async_set_mute(self, zone: MoIPZone, mute: bool) -> None:
        """Mute/unmute a zone.

        MoIP mute is an ``AudioMuteList`` (list of output ports), not a bool:
        mute = the audio_rx's ``supported_output`` ports; unmute = empty list.
        """
        raise NotImplementedError

    async def async_select_source(self, group_rx_id: int, group_tx_id: int | None) -> None:
        """Route a source to a zone by setting ``group_rx.associations.paired_tx``.

        ``group_tx_id`` of ``None`` unpairs the zone (off).
        """
        raise NotImplementedError
