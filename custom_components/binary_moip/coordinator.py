"""DataUpdateCoordinator for the Binary MoIP integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BinaryMoIPAuthError, BinaryMoIPClient, BinaryMoIPError, MoIPZone
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Typed config entry: runtime_data holds the coordinator.
type BinaryMoIPConfigEntry = ConfigEntry[BinaryMoIPDataUpdateCoordinator]


class BinaryMoIPDataUpdateCoordinator(DataUpdateCoordinator[dict[str, MoIPZone]]):
    """Poll the MoIP controller and cache zone state for entities.

    Data shape: ``{group_id: MoIPZone}``. A later stage will layer a WebSocket
    subscription on top of this polling baseline.
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

    async def _async_update_data(self) -> dict[str, MoIPZone]:
        """Fetch the current zone topology and state from the controller."""
        try:
            zones = await self.client.async_get_zones()
        except BinaryMoIPAuthError as err:
            # Trigger reauth flow via ConfigEntryAuthFailed in a later stage.
            raise UpdateFailed(f"Authentication error: {err}") from err
        except BinaryMoIPError as err:
            raise UpdateFailed(f"Error communicating with MoIP controller: {err}") from err

        return {zone.group_id: zone for zone in zones}
