"""Media player platform for the Binary MoIP integration.

One media_player entity is created per logical zone (``group_rx``), using the
user-assigned zone name rather than the hardware ``audio_rx`` default name.
"""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BinaryMoIPConfigEntry, BinaryMoIPDataUpdateCoordinator

# Features the entity will eventually advertise. Wired up in a later stage.
SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BinaryMoIPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up media_player entities, one per logical zone."""
    coordinator = entry.runtime_data
    async_add_entities(
        BinaryMoIPMediaPlayer(coordinator, group_id)
        for group_id in coordinator.data
    )


class BinaryMoIPMediaPlayer(
    CoordinatorEntity[BinaryMoIPDataUpdateCoordinator], MediaPlayerEntity
):
    """A media_player representing a single Binary MoIP logical zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(
        self,
        coordinator: BinaryMoIPDataUpdateCoordinator,
        group_id: str,
    ) -> None:
        """Initialize the zone entity."""
        super().__init__(coordinator)
        self._group_id = group_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{group_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            manufacturer=MANUFACTURER,
            name=self._zone.name,
        )

    @property
    def _zone(self):
        """Return the current MoIPZone for this entity from coordinator data."""
        return self.coordinator.data[self._group_id]

    # State / command handlers (volume, mute, source) land in a later stage.
