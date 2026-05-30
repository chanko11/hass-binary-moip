"""Media player platform for the Binary MoIP integration.

One media_player entity is created per logical zone (``group_rx``). The zone's
name comes from ``group_rx.settings.name`` (or a user override from the options
flow) — never the hardware ``audio_rx.label``. See docs/naming-and-discovery.md.
"""

from __future__ import annotations

from collections import Counter

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MoIPSource, MoIPTopology, MoIPZone
from .const import (
    DOMAIN,
    MANUFACTURER,
    OPT_ENABLED,
    OPT_LABEL,
    OPT_SOURCES,
    OPT_ZONES,
    SOURCE_NONE,
    STATE_STREAMING,
    STATE_UNCONNECTED,
)
from .coordinator import BinaryMoIPConfigEntry, BinaryMoIPDataUpdateCoordinator

SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BinaryMoIPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up media_player entities, one per enabled logical zone.

    All discovered zones are candidates; the options flow decides which are
    enabled (default: enabled).
    """
    coordinator = entry.runtime_data
    zone_opts = entry.options.get(OPT_ZONES, {})

    def _enabled(group_id: int) -> bool:
        return zone_opts.get(str(group_id), {}).get(OPT_ENABLED, True)

    enabled_ids = {gid for gid in coordinator.data.zones if _enabled(gid)}

    # On reload, drop registry entries for zones that are now disabled or gone,
    # so they don't linger as unavailable entities.
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "media_player" or not reg_entry.unique_id.startswith(prefix):
            continue
        try:
            gid = int(reg_entry.unique_id[len(prefix):])
        except ValueError:
            continue
        if gid not in enabled_ids:
            registry.async_remove(reg_entry.entity_id)

    # Remove the legacy per-amp devices (identifier "..._unit_<id>"); zones are
    # now modeled as one device each so they can carry their own area.
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if any(f"{entry.entry_id}_unit_" in ident for _, ident in device.identifiers):
            dev_reg.async_remove_device(device.id)

    async_add_entities(
        BinaryMoIPMediaPlayer(coordinator, group_id) for group_id in enabled_ids
    )


def _source_label(source: MoIPSource, override: str | None) -> str:
    """Build a human-friendly source label.

    Uses the options-flow override if set; otherwise synthesizes from parent
    unit + hardware label + input type, since controller source names are often
    non-unique defaults (e.g. ``TX-...``). Uniqueness across sources is enforced
    separately by :func:`_build_source_maps`.
    """
    if override:
        return override
    base = source.hw_label or source.name
    label = f"{source.unit_name} – {base}" if source.unit_name else base
    if source.input_type and source.input_type.lower() not in label.lower():
        label = f"{label} ({source.input_type})"
    return label


def _build_source_maps(
    data: MoIPTopology, options: dict
) -> tuple[dict[int, str], dict[str, int]]:
    """Return (group_tx_id -> label, label -> group_tx_id), labels made unique.

    Covers ALL sources (so the current source can be displayed even if it is
    disabled); callers filter to enabled sources for the selectable list.
    """
    source_opts = options.get(OPT_SOURCES, {})
    labels: dict[int, str] = {}
    for sid, source in data.sources.items():
        override = source_opts.get(str(sid), {}).get(OPT_LABEL)
        labels[sid] = _source_label(source, override)

    # Disambiguate any collisions (e.g. a streamer exposing 4 identical inputs).
    counts = Counter(labels.values())
    seen: dict[str, int] = {}
    for sid, label in list(labels.items()):
        if counts[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            labels[sid] = f"{label} #{seen[label]}"

    reverse = {label: sid for sid, label in labels.items()}
    return labels, reverse


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

    @property
    def _zone(self) -> MoIPZone:
        """Return the current MoIPZone for this entity from coordinator data."""
        return self.coordinator.data.zones[self._group_id]

    @property
    def _options(self) -> dict:
        return self.coordinator.config_entry.options

    @property
    def available(self) -> bool:
        """Available when polling succeeds, the zone exists, and it's connected."""
        return (
            super().available
            and self._group_id in self.coordinator.data.zones
            and self._zone.state != STATE_UNCONNECTED
        )

    @property
    def name(self) -> str:
        """Zone name: options-flow label override, else group_rx name."""
        override = (
            self._options.get(OPT_ZONES, {})
            .get(str(self._group_id), {})
            .get(OPT_LABEL)
        )
        return override or self._zone.name

    @property
    def device_info(self) -> DeviceInfo:
        """One device per zone, so each is independently area-assignable."""
        zone = self._zone
        # Each zone is its own device so it can be assigned to its own HA area
        # (the amps span multiple rooms, so grouping by amp would be wrong).
        # The parent amp's model/name are carried for reference only.
        unit = self.coordinator.data.units.get(zone.unit_id) if zone.unit_id else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self.name,
            manufacturer=MANUFACTURER,
            model=unit.model if unit is not None else None,
        )

    @property
    def state(self) -> MediaPlayerState:
        """Map MoIP zone state to a media_player state.

        Never OFF: MoIP zones have no power concept, and HA hides the volume /
        source controls for an "off" player. PLAYING when streaming, otherwise
        IDLE — so the source dropdown and volume slider are always available.
        """
        if self._zone.state == STATE_STREAMING:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        """Current volume scaled to 0.0–1.0 using the zone's supported range."""
        zone = self._zone
        if zone.volume is None:
            return None
        lo, hi = zone.volume_range or (0.0, 100.0)
        if hi <= lo:
            return None
        return max(0.0, min(1.0, (zone.volume - lo) / (hi - lo)))

    @property
    def is_volume_muted(self) -> bool | None:
        """Whether the zone is muted."""
        return self._zone.muted

    @property
    def source(self) -> str:
        """Currently routed source label, or SOURCE_NONE when unpaired."""
        zone = self._zone
        if zone.paired_tx_id is None:
            return SOURCE_NONE
        labels, _ = _build_source_maps(self.coordinator.data, self._options)
        return labels.get(zone.paired_tx_id, SOURCE_NONE)

    @property
    def source_list(self) -> list[str]:
        """Selectable sources (enabled ones) plus a "None" entry to unpair."""
        labels, _ = _build_source_maps(self.coordinator.data, self._options)
        source_opts = self._options.get(OPT_SOURCES, {})
        enabled = sorted(
            label
            for sid, label in labels.items()
            if source_opts.get(str(sid), {}).get(OPT_ENABLED, True)
        )
        return [SOURCE_NONE, *enabled]

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume (0.0–1.0)."""
        await self.coordinator.client.async_set_volume(self._zone, volume)
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the zone."""
        await self.coordinator.client.async_set_mute(self._zone, mute)
        await self.coordinator.async_request_refresh()

    async def async_select_source(self, source: str) -> None:
        """Route the named source to this zone, or unpair it for SOURCE_NONE."""
        if source == SOURCE_NONE:
            group_tx_id = None
        else:
            _, reverse = _build_source_maps(self.coordinator.data, self._options)
            if source not in reverse:
                raise ValueError(f"Unknown source: {source}")
            group_tx_id = reverse[source]
        await self.coordinator.client.async_select_source(self._group_id, group_tx_id)
        await self.coordinator.async_request_refresh()
