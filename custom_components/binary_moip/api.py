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
class MoIPZone:
    """A logical zone derived from a ``group_rx`` entry.

    This is the unit that becomes a Home Assistant media_player entity.
    """

    group_id: str
    name: str
    # IDs of the hardware audio_rx outputs backing this group.
    audio_rx_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


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

    async def async_get_zones(self) -> list[MoIPZone]:
        """Return logical zones by walking ``group_rx``.

        This is the canonical source of zone naming for the integration. Each
        returned :class:`MoIPZone` becomes one media_player entity.
        """
        raise NotImplementedError

    async def async_set_volume(self, group_id: str, volume: float) -> None:
        """Set the volume (0.0–1.0) for a logical zone."""
        raise NotImplementedError

    async def async_set_mute(self, group_id: str, mute: bool) -> None:
        """Mute or unmute a logical zone."""
        raise NotImplementedError

    async def async_select_source(self, group_id: str, source_id: str) -> None:
        """Route an input source to a logical zone."""
        raise NotImplementedError
