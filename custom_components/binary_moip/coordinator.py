"""DataUpdateCoordinator for the Binary MoIP integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BinaryMoIPAuthError, BinaryMoIPClient, BinaryMoIPError, MoIPTopology
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Typed config entry: runtime_data holds the coordinator.
type BinaryMoIPConfigEntry = ConfigEntry[BinaryMoIPDataUpdateCoordinator]


class BinaryMoIPDataUpdateCoordinator(DataUpdateCoordinator[MoIPTopology]):
    """Poll the MoIP controller and cache topology/state for entities.

    Data is a :class:`MoIPTopology` (units, zones keyed by group_rx id, sources
    keyed by group_tx id). A later stage will layer a WebSocket subscription on
    top of this polling baseline.
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
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> MoIPTopology:
        """Fetch the current topology and zone/source state from the controller."""
        try:
            return await self.client.async_discover()
        except BinaryMoIPAuthError as err:
            # Trigger reauth flow via ConfigEntryAuthFailed in a later stage.
            raise UpdateFailed(f"Authentication error: {err}") from err
        except BinaryMoIPError as err:
            raise UpdateFailed(f"Error communicating with MoIP controller: {err}") from err
