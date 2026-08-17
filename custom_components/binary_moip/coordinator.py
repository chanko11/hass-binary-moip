"""DataUpdateCoordinator for the Binary MoIP integration."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import WSMsgType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BinaryMoIPAuthError, BinaryMoIPClient, BinaryMoIPError, MoIPTopology
from .const import DOMAIN, FALLBACK_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

# Coalesce bursts of websocket change events (e.g. a volume ramp) into at most
# one full refresh per this many seconds; the first event refreshes immediately.
_WS_REFRESH_COOLDOWN = 2.0
# Websocket reconnect backoff (seconds): exponential, capped.
_WS_BACKOFF_START = 2.0
_WS_BACKOFF_MAX = 60.0

# Typed config entry: runtime_data holds the coordinator.
type BinaryMoIPConfigEntry = ConfigEntry[BinaryMoIPDataUpdateCoordinator]


def _carry_forward(new: MoIPTopology, prev: MoIPTopology) -> None:
    """Backfill transient-failure gaps in ``new`` with last-known values.

    A zone/source/unit the controller failed to return this poll is absent from
    ``new`` (api._gather skips failures); and a zone whose backing ``audio_rx``
    failed is present but has ``volume is None``. Rather than let those show as
    gone / volume-0, keep the previous poll's value until a poll succeeds again.
    """
    for gid, pz in prev.zones.items():
        nz = new.zones.get(gid)
        if nz is None:
            new.zones[gid] = pz  # group_rx skipped this poll -> keep last-known
        elif nz.volume is None and pz.volume is not None:
            # backing audio_rx skipped -> keep last-known volume/bounds/mute
            nz.volume = pz.volume
            nz.max_volume = pz.max_volume
            nz.volume_range = pz.volume_range
            nz.muted = pz.muted
            nz.mute_ports = pz.mute_ports
    for sid, ps in prev.sources.items():
        new.sources.setdefault(sid, ps)
    for uid, pu in prev.units.items():
        new.units.setdefault(uid, pu)


class BinaryMoIPDataUpdateCoordinator(DataUpdateCoordinator[MoIPTopology]):
    """Coordinate MoIP state via a change-event websocket, with polling fallback.

    Data is a :class:`MoIPTopology` (units, zones keyed by group_rx id, sources
    keyed by group_tx id). The websocket pushes change notifications that
    trigger a (debounced) refresh; polling at :data:`FALLBACK_SCAN_INTERVAL`
    is a safety net for when the socket is down.
    """

    config_entry: BinaryMoIPConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BinaryMoIPConfigEntry,
        client: BinaryMoIPClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=FALLBACK_SCAN_INTERVAL,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=_WS_REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.ws_connected = False

    async def _async_update_data(self) -> MoIPTopology:
        """Fetch the current topology and zone/source state from the controller."""
        try:
            topology = await self.client.async_discover()
        except BinaryMoIPAuthError as err:
            # Trigger reauth flow via ConfigEntryAuthFailed in a later stage.
            raise UpdateFailed(f"Authentication error: {err}") from err
        except BinaryMoIPError as err:
            raise UpdateFailed(f"Error communicating with MoIP controller: {err}") from err
        # Discovery skips objects the controller transiently 5xx's (see api._gather).
        # Carry forward the previous poll's values for those gaps so a blip doesn't
        # blank a zone's volume (slider jumps to 0) or make it briefly vanish.
        if self.data is not None:
            _carry_forward(topology, self.data)
        return topology

    def start_websocket(self) -> None:
        """Start the background websocket listener, tied to the entry lifecycle."""
        self.config_entry.async_create_background_task(
            self.hass, self._ws_listen(), name=f"{DOMAIN}_ws"
        )

    async def _ws_listen(self) -> None:
        """Maintain the change-event websocket, refreshing on relevant changes.

        Reconnects with exponential backoff. Cancelled automatically when the
        config entry unloads (background task).
        """
        backoff = _WS_BACKOFF_START
        while True:
            try:
                ws = await self.client.async_ws_connect()
            except Exception as err:  # noqa: BLE001 - any connect failure -> retry
                _LOGGER.debug("MoIP websocket connect failed: %s", err)
            else:
                _LOGGER.debug("MoIP websocket connected")
                self.ws_connected = True
                backoff = _WS_BACKOFF_START
                try:
                    await self._ws_consume(ws)
                except asyncio.CancelledError:
                    await ws.close()
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("MoIP websocket error: %s", err)
                finally:
                    self.ws_connected = False
                    _LOGGER.debug("MoIP websocket disconnected")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _WS_BACKOFF_MAX)

    async def _ws_consume(self, ws) -> None:
        """Read change messages and request a refresh on any non-ping change."""
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
                    break
                continue
            try:
                changes = msg.json().get("changes", [])
            except ValueError:
                continue
            if any(
                c.get("kind") in ("added", "removed", "modified")
                and "/moip/" in (c.get("url") or "")
                for c in changes
            ):
                await self.async_request_refresh()
