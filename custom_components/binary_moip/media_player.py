"""Media player platform for the Binary MoIP integration.

One media_player entity is created per logical zone (``group_rx``). The zone's
name comes from ``group_rx.settings.name`` (or a user override from the options
flow) — never the hardware ``audio_rx.label``. See docs/naming-and-discovery.md.
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

from .api import MoIPZone
from .const import DOMAIN, MANUFACTURER, OPT_ENABLED, OPT_ZONES
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
    """Set up media_player entities, one per enabled logical zone.

    All discovered zones are candidates; the options flow decides which are
    enabled. Disabled zones are skipped here (default: enabled).
    """
    coordinator = entry.runtime_data
    zone_opts = entry.options.get(OPT_ZONES, {})

    def _enabled(group_id: int) -> bool:
        return zone_opts.get(str(group_id), {}).get(OPT_ENABLED, True)

    async_add_entities(
        BinaryMoIPMediaPlayer(coordinator, group_id)
        for group_id in coordinator.data.zones
        if _enabled(group_id)
    )


class BinaryMoIPMediaPlayer(
    CoordinatorEntity[BinaryMoIPDataUpdateCoordinator], MediaPlayerEntity
):
    """A media_player representing a single Binary MoIP logical zone."""

    _attr_has_entity_name = False
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = SUPPORTED_FEATURES

    def __init__(
        self,
        coordinator: BinaryMoIPDataUpdateCoordinator,
        group_id: int,
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
    def _zone(self) -> MoIPZone:
        """Return the current MoIPZone for this entity from coordinator data."""
        return self.coordinator.data.zones[self._group_id]

    @property
    def name(self) -> str:
        """Friendly zone name: options-flow label override, else group_rx name."""
        # TODO: prefer options[OPT_ZONES][group_id][OPT_LABEL] when set.
        return self._zone.name

    # Remaining handlers (state, volume_level, is_volume_muted, source,
    # source_list, async_set_volume_level, async_mute_volume,
    # async_select_source) land in a later stage. source_list is built from the
    # coordinator's enabled sources with synthesized friendly labels; `source`
    # maps the zone's paired_tx_id back to that label.
