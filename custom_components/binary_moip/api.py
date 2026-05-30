"""API client for the Binary MoIP controller.

Wraps the Binary MoIP REST API v1.3.0:
https://help.snapone.com/moip-ig/Content/Binary%20MoIP%20Topics/API%20v1.3.0.html

Design notes:
- JWT auth. ``authenticate`` POSTs credentials and stores an access token; the
  response has no refresh token, so renewal is a fresh login. ``_request``
  re-authenticates once on 401 and retries.
- The API separates *logical zones* (``group_rx`` — user-named, the unit a
  human thinks of as "the Kitchen") from *hardware outputs* (``audio_rx`` —
  default names tied to physical RX hardware). This client surfaces zones by
  walking ``group_rx`` so entities get the user's zone names, not the hardware
  defaults.
- Discovery enumerates via units: ``GET /unit`` → each unit's
  ``associations.group.rx[]`` / ``group.tx[]`` → ``group_rx``/``group_tx``
  details → backing ``audio_rx``/``audio_tx``. Validated to reproduce all
  zones/sources on the reference system. See docs/naming-and-discovery.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
)

_LOGGER = logging.getLogger(__name__)

# API paths.
_LOGIN = "/api/v1/base/auth/login"
_UNIT_LIST = "/api/v1/moip/unit"
_UNIT = "/api/v1/moip/unit/{id}"
_GROUP_RX = "/api/v1/moip/group_rx/{id}"
_GROUP_TX = "/api/v1/moip/group_tx/{id}"
_AUDIO_RX = "/api/v1/moip/audio_rx/{id}"
_AUDIO_TX = "/api/v1/moip/audio_tx/{id}"
_CHANGE_WS = "/api/v1/moip/change"

# Re-login this many seconds before the token's stated expiry.
_TOKEN_REFRESH_MARGIN = 30.0
# Per-request timeout (seconds).
_REQUEST_TIMEOUT = 15.0


def aiohttp_timeout() -> ClientTimeout:
    """Return the standard per-request timeout."""
    return ClientTimeout(total=_REQUEST_TIMEOUT)


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
    # Supported output ports (audio_rx.settings.supported_output); the set we
    # write to settings.mute to mute, or [] to unmute.
    mute_ports: list[str] = field(default_factory=list)
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

        # The login response carries only an access token + expiry (no refresh
        # token), so re-authentication is a fresh login with the stored
        # credentials. Track expiry to know when to re-login.
        self._access_token: str | None = None
        self._token_expires_at: float | None = None

    @property
    def base_url(self) -> str:
        """Return the base URL for REST requests."""
        return f"https://{self._host}:{self._port}"

    @property
    def _ssl(self) -> bool:
        """Per-request ssl arg: False disables verification (self-signed certs)."""
        return self._verify_ssl

    async def authenticate(self) -> None:
        """Log in (POST /api/v1/base/auth/login) and store the JWT access token.

        The response is ``{accessToken, tokenType, expiresIn}`` — there is no
        refresh token, so token renewal is just calling this again.

        Raises:
            BinaryMoIPAuthError: credentials rejected.
            BinaryMoIPConnectionError: controller unreachable.
        """
        url = f"{self.base_url}{_LOGIN}"
        payload = {"username": self._username, "password": self._password}
        try:
            async with self._session.post(
                url,
                json=payload,
                ssl=self._ssl,
                timeout=aiohttp_timeout(),
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise BinaryMoIPAuthError(
                        f"Login rejected (HTTP {resp.status})"
                    )
                resp.raise_for_status()
                data = await resp.json()
        except BinaryMoIPAuthError:
            raise
        except (ClientError, asyncio.TimeoutError) as err:
            raise BinaryMoIPConnectionError(f"Login request failed: {err}") from err

        token = data.get("accessToken")
        if not token:
            raise BinaryMoIPAuthError("Login response missing accessToken")
        self._access_token = token
        expires_in = float(data.get("expiresIn", 3600))
        self._token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_MARGIN

    async def _ensure_token(self) -> None:
        """Authenticate if there is no valid (non-expired) token."""
        if self._access_token is None or (
            self._token_expires_at is not None
            and time.monotonic() >= self._token_expires_at
        ):
            await self.authenticate()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated request, re-authenticating once on 401.

        Returns parsed JSON, or ``None`` for empty/204 responses.
        """
        await self._ensure_token()
        url = f"{self.base_url}{path}"

        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._access_token}"}
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json,
                    headers=headers,
                    ssl=self._ssl,
                    timeout=aiohttp_timeout(),
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        # Token may have been invalidated server-side; re-login.
                        self._access_token = None
                        await self.authenticate()
                        continue
                    if resp.status == 401:
                        raise BinaryMoIPAuthError("Unauthorized after re-login")
                    resp.raise_for_status()
                    if resp.status == 204 or resp.content_length == 0:
                        return None
                    return await resp.json()
            except BinaryMoIPAuthError:
                raise
            except ClientResponseError as err:
                raise BinaryMoIPError(
                    f"{method} {path} failed: HTTP {err.status} {err.message}"
                ) from err
            except (ClientError, asyncio.TimeoutError) as err:
                raise BinaryMoIPConnectionError(
                    f"{method} {path} failed: {err}"
                ) from err

        # Unreachable (loop always returns or raises), but satisfies type checker.
        raise BinaryMoIPError(f"{method} {path} failed")

    async def async_discover(self) -> MoIPTopology:
        """Discover the full topology: units, zones (group_rx), sources (group_tx).

        Canonical naming source for the integration:
        - zones from ``group_rx.settings.name`` (NOT ``audio_rx.label``),
        - sources from ``group_tx.settings.name``,
        dereferencing ``associations`` to link zones↔audio_rx↔paired source.

        Enumerates via unit associations (validated to reproduce every
        zone/source on the reference system). See docs/naming-and-discovery.md.
        """
        topology = MoIPTopology()

        # 1) Units, fetched concurrently.
        id_list = await self._request("GET", _UNIT_LIST)
        unit_ids = [int(i) for i in (id_list or {}).get("items", [])]
        unit_objs = await asyncio.gather(
            *(self._request("GET", _UNIT.format(id=uid)) for uid in unit_ids)
        )

        group_rx_ids: list[int] = []
        group_tx_ids: list[int] = []
        for raw in unit_objs:
            unit = _parse_unit(raw)
            topology.units[unit.unit_id] = unit
            assoc = (raw.get("associations") or {}).get("group") or {}
            group_rx_ids += [int(i) for i in assoc.get("rx", [])]
            group_tx_ids += [int(i) for i in assoc.get("tx", [])]

        # 2) Zones (group_rx) and sources (group_tx), concurrently.
        rx_objs, tx_objs = await asyncio.gather(
            asyncio.gather(*(self._request("GET", _GROUP_RX.format(id=i)) for i in group_rx_ids)),
            asyncio.gather(*(self._request("GET", _GROUP_TX.format(id=i)) for i in group_tx_ids)),
        )

        zones = [_parse_zone(raw, topology.units) for raw in rx_objs]
        sources = [_parse_source(raw, topology.units) for raw in tx_objs]

        # 3) Backing audio_rx (zone volume/mute/state) and audio_tx (source
        #    hw label + input type), concurrently.
        audio_rx_objs = await asyncio.gather(
            *(
                self._request("GET", _AUDIO_RX.format(id=z.audio_rx_id))
                if z.audio_rx_id is not None
                else _none()
                for z in zones
            )
        )
        audio_tx_objs = await asyncio.gather(
            *(
                self._request("GET", _AUDIO_TX.format(id=s.audio_tx_id))
                if s.audio_tx_id is not None
                else _none()
                for s in sources
            )
        )

        for zone, arx in zip(zones, audio_rx_objs):
            _apply_audio_rx(zone, arx)
            topology.zones[zone.group_id] = zone
        for source, atx in zip(sources, audio_tx_objs):
            _apply_audio_tx(source, atx)
            topology.sources[source.group_id] = source

        return topology

    async def async_get_units(self) -> list[MoIPUnit]:
        """List physical units (GET /moip/unit)."""
        return list((await self.async_discover()).units.values())

    async def async_get_zones(self) -> list[MoIPZone]:
        """List logical zones by walking ``group_rx`` and their backing audio_rx."""
        return list((await self.async_discover()).zones.values())

    async def async_get_sources(self) -> list[MoIPSource]:
        """List logical sources by walking ``group_tx``."""
        return list((await self.async_discover()).sources.values())

    async def async_set_volume(self, zone: MoIPZone, volume: float) -> None:
        """Set volume for a zone. ``volume`` is HA-scale 0.0–1.0.

        Scales into the zone's ``volume_range`` (clamped by ``max_volume``) and
        PUTs ``settings.volume`` on the backing ``audio_rx``.
        """
        if zone.audio_rx_id is None:
            raise BinaryMoIPError(f"Zone {zone.group_id} has no audio_rx to set volume")
        lo, hi = zone.volume_range or (0.0, 100.0)
        target = lo + max(0.0, min(1.0, volume)) * (hi - lo)
        if zone.max_volume is not None:
            target = min(target, zone.max_volume)
        await self._request(
            "PUT", _AUDIO_RX.format(id=zone.audio_rx_id), json={"settings": {"volume": target}}
        )

    async def async_set_mute(self, zone: MoIPZone, mute: bool) -> None:
        """Mute/unmute a zone.

        MoIP mute is an ``AudioMuteList`` (list of output ports), not a bool:
        mute = the audio_rx's ``supported_output`` ports; unmute = empty list.
        """
        if zone.audio_rx_id is None:
            raise BinaryMoIPError(f"Zone {zone.group_id} has no audio_rx to mute")
        ports = zone.mute_ports if mute else []
        await self._request(
            "PUT", _AUDIO_RX.format(id=zone.audio_rx_id), json={"settings": {"mute": ports}}
        )

    async def async_select_source(self, group_rx_id: int, group_tx_id: int | None) -> None:
        """Route a source to a zone by setting ``group_rx.associations.paired_tx``.

        ``group_tx_id`` of ``None`` unpairs the zone (off).
        """
        await self._request(
            "PUT",
            _GROUP_RX.format(id=group_rx_id),
            json={"associations": {"paired_tx": group_tx_id}},
        )

    async def async_ws_connect(self) -> ClientWebSocketResponse:
        """Open the change-event websocket.

        The controller can't read an Authorization header here, so the JWT is
        smuggled via the WS subprotocol as ``Bearer.{token}`` (per the API spec).
        Messages are ``{"changes": [{"url": ..., "kind": ...}]}``.
        """
        await self._ensure_token()
        url = f"wss://{self._host}:{self._port}{_CHANGE_WS}"
        return await self._session.ws_connect(
            url,
            protocols=(f"Bearer.{self._access_token}",),
            ssl=self._ssl,
            heartbeat=30,
        )


async def _none() -> None:
    """Awaitable that yields None (placeholder in gather for missing ids)."""
    return None


def _parse_unit(raw: dict[str, Any]) -> MoIPUnit:
    """Build a MoIPUnit from a raw /unit/{id} response."""
    settings = raw.get("settings") or {}
    status = raw.get("status") or {}
    return MoIPUnit(
        unit_id=int(raw["id"]),
        name=settings.get("name") or f"Unit {raw['id']}",
        model=status.get("model"),
        mac=status.get("mac"),
        state=status.get("unit_state"),
        raw=raw,
    )


def _parse_zone(raw: dict[str, Any], units: dict[int, MoIPUnit]) -> MoIPZone:
    """Build a MoIPZone from a raw /group_rx/{id} response."""
    settings = raw.get("settings") or {}
    assoc = raw.get("associations") or {}
    status = raw.get("status") or {}
    unit_id = _opt_int(assoc.get("unit"))
    name = settings.get("name") or (
        f"{units[unit_id].name} zone" if unit_id in units else f"Zone {raw['id']}"
    )
    return MoIPZone(
        group_id=int(raw["id"]),
        name=name,
        unit_id=unit_id,
        audio_rx_id=_opt_int(assoc.get("audio_rx")),
        paired_tx_id=_opt_int(assoc.get("paired_tx")),
        state=status.get("state"),
        raw=raw,
    )


def _parse_source(raw: dict[str, Any], units: dict[int, MoIPUnit]) -> MoIPSource:
    """Build a MoIPSource from a raw /group_tx/{id} response."""
    settings = raw.get("settings") or {}
    assoc = raw.get("associations") or {}
    status = raw.get("status") or {}
    unit_id = _opt_int(assoc.get("unit"))
    return MoIPSource(
        group_id=int(raw["id"]),
        name=settings.get("name") or f"Source {raw['id']}",
        unit_id=unit_id,
        unit_name=units[unit_id].name if unit_id in units else None,
        audio_tx_id=_opt_int(assoc.get("audio_tx")),
        state=status.get("state"),
        raw=raw,
    )


def _apply_audio_rx(zone: MoIPZone, raw: dict[str, Any] | None) -> None:
    """Populate a zone's volume/mute/state from its backing audio_rx response."""
    if not raw:
        return
    settings = raw.get("settings") or {}
    status = raw.get("status") or {}
    zone.volume = settings.get("volume")
    zone.max_volume = settings.get("maxvolume")
    rng = (settings.get("supported_volume") or {}).get("range")
    if isinstance(rng, list) and len(rng) == 2:
        zone.volume_range = (float(rng[0]), float(rng[1]))
    zone.mute_ports = list(settings.get("supported_output") or [])
    zone.muted = bool(settings.get("mute"))  # non-empty mute list = muted
    if status.get("state"):
        zone.state = status["state"]


def _apply_audio_tx(source: MoIPSource, raw: dict[str, Any] | None) -> None:
    """Populate a source's hardware label/input type from its audio_tx response."""
    if not raw:
        return
    settings = raw.get("settings") or {}
    source.hw_label = raw.get("label")
    # Real firmware returns settings.source as a list of port(s) (e.g.
    # ["toslink"]) even though the spec types it as a single AudioPort enum.
    # Normalize to a single human-friendly string.
    src = settings.get("source")
    if isinstance(src, list):
        src = next((p for p in src if p), None)
    source.input_type = src


def _opt_int(value: Any) -> int | None:
    """Coerce an association id to int, treating null/empty as None."""
    if value in (None, "", 0):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
