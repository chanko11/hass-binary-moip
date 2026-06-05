"""Media player platform for the Binary MoIP integration.

Two kinds of media_player entity, both driven entirely by the coordinator's
topology (the controller's routing is the single source of truth):

- One per logical zone (``group_rx``) — volume, mute, source-select. Its name
  comes from ``group_rx.settings.name`` (or an options override), never the
  hardware ``audio_rx.label``.
- One per source (``group_tx``) — a grouping-only player for source-first
  control: join routes zones to the source, unjoin unpairs them.

Grouping convention: the source is the group leader. Every member of a group
(the source and each zone routed to it) reports the SAME ``group_members`` list
with the source's entity_id first, per HA's "leader is element 0" convention.

See docs/naming-and-discovery.md.
"""

from __future__ import annotations

from collections import Counter

from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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

ZONE_SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.GROUPING
)
# A source is grouping-only for now (no transport / no backing player).
SOURCE_SUPPORTED_FEATURES = MediaPlayerEntityFeature.GROUPING


def _zone_unique_id(entry_id: str, group_rx_id: int) -> str:
    """Unique id for a zone entity (unchanged scheme, kept for stability)."""
    return f"{entry_id}_{group_rx_id}"


def _source_unique_id(entry_id: str, group_tx_id: int) -> str:
    """Unique id for a source entity (``source_`` segment avoids zone clashes)."""
    return f"{entry_id}_source_{group_tx_id}"


def _zone_group_id(entry_id: str, unique_id: str) -> int | None:
    """Recover a zone's ``group_rx`` id from its unique id, else None.

    Returns None for source unique ids and anything not matching the scheme.
    """
    prefix = f"{entry_id}_"
    if not unique_id.startswith(prefix):
        return None
    suffix = unique_id[len(prefix):]
    if suffix.startswith("source_"):
        return None
    try:
        return int(suffix)
    except ValueError:
        return None


def _resolve_entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    """Look up a media_player entity_id by our unique id (None if not registered)."""
    return er.async_get(hass).async_get_entity_id(
        MEDIA_PLAYER_DOMAIN, DOMAIN, unique_id
    )


def _zone_entity_ids_for_source(
    hass: HomeAssistant,
    coordinator: BinaryMoIPDataUpdateCoordinator,
    entry_id: str,
    group_tx_id: int,
) -> list[str]:
    """Sorted entity_ids of the (registered) zones currently routed to a source."""
    eids: list[str] = []
    for gid, zone in coordinator.data.zones.items():
        if zone.paired_tx_id != group_tx_id:
            continue
        eid = _resolve_entity_id(hass, _zone_unique_id(entry_id, gid))
        if eid is not None:
            eids.append(eid)
    return sorted(eids)


async def _route_zone_entities(
    hass: HomeAssistant,
    coordinator: BinaryMoIPDataUpdateCoordinator,
    entry_id: str,
    member_entity_ids: list[str],
    group_tx_id: int | None,
) -> None:
    """Route each given zone entity to ``group_tx_id`` (None unpairs), then refresh.

    Members that aren't this integration's zones are ignored. Uses only the
    existing "set zone source" primitive — no new MoIP API calls.
    """
    registry = er.async_get(hass)
    for member in member_entity_ids:
        reg_entry = registry.async_get(member)
        if reg_entry is None:
            continue
        gid = _zone_group_id(entry_id, reg_entry.unique_id)
        if gid is not None and gid in coordinator.data.zones:
            await coordinator.client.async_select_source(gid, group_tx_id)
    await coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BinaryMoIPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up media_player entities: one per enabled zone and one per enabled source.

    All discovered zones (``group_rx``) and sources (``group_tx``) are
    candidates; the options flow decides which are enabled (default: enabled).
    """
    coordinator = entry.runtime_data
    zone_opts = entry.options.get(OPT_ZONES, {})
    source_opts = entry.options.get(OPT_SOURCES, {})

    def _enabled(opts: dict, item_id: int) -> bool:
        return opts.get(str(item_id), {}).get(OPT_ENABLED, True)

    enabled_zones = {gid for gid in coordinator.data.zones if _enabled(zone_opts, gid)}
    enabled_sources = {
        sid for sid in coordinator.data.sources if _enabled(source_opts, sid)
    }

    # On reload, drop registry entries (zone or source) that are now disabled or
    # gone, so they don't linger as unavailable entities.
    desired = {_zone_unique_id(entry.entry_id, gid) for gid in enabled_zones} | {
        _source_unique_id(entry.entry_id, sid) for sid in enabled_sources
    }
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == MEDIA_PLAYER_DOMAIN and reg_entry.unique_id not in desired:
            registry.async_remove(reg_entry.entity_id)

    # Remove the legacy per-amp devices (identifier "..._unit_<id>"); zones are
    # now modeled as one device each so they can carry their own area.
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if any(f"{entry.entry_id}_unit_" in ident for _, ident in device.identifiers):
            dev_reg.async_remove_device(device.id)

    entities: list[MediaPlayerEntity] = [
        BinaryMoIPMediaPlayer(coordinator, gid) for gid in enabled_zones
    ]
    entities += [
        BinaryMoIPSourceMediaPlayer(coordinator, sid) for sid in enabled_sources
    ]
    async_add_entities(entities)


def _is_default_source_name(name: str | None) -> bool:
    """Whether a ``group_tx`` name is a controller default rather than user-set.

    The controller auto-names transmitters ``TX-<mac/serial>[-<n>]`` (e.g.
    ``TX-D46A9128261A-1``); those are useless as labels. Any other name is one
    the user deliberately set on the controller (e.g. ``Record Player``).
    """
    return not name or name.upper().startswith("TX-")


def _source_label(source: MoIPSource, override: str | None) -> str:
    """Build a human-friendly source label.

    Priority: options-flow override, then the controller's own source name when
    it's a real (non-default) name, then a synthesized label from parent unit +
    hardware label + input type (for the ``TX-...`` defaults). Uniqueness across
    sources is enforced separately by :func:`_build_source_maps`.
    """
    if override:
        return override
    if not _is_default_source_name(source.name):
        return source.name
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
    _attr_supported_features = ZONE_SUPPORTED_FEATURES

    def __init__(
        self,
        coordinator: BinaryMoIPDataUpdateCoordinator,
        group_id: int,
    ) -> None:
        """Initialize the zone entity."""
        super().__init__(coordinator)
        self._group_id = group_id
        self._entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = _zone_unique_id(self._entry_id, group_id)

    async def async_added_to_hass(self) -> None:
        """Nudge peers to recompute group_members now that we're registered.

        group_members resolves peers by entity_id, so entities added earlier
        couldn't see us yet; refresh them once we have an entity_id.
        """
        await super().async_added_to_hass()
        self.coordinator.async_update_listeners()

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

    # --- grouping (source-first; the routed source is the group leader) ------

    @property
    def group_members(self) -> list[str]:
        """The group this zone belongs to: leader (its source) first, then peers.

        A zone's group is every zone sharing its current source, led by that
        source's entity_id. An unpaired zone (or one whose source isn't an
        enabled entity) is a lone group of just itself.
        """
        tx_id = self._zone.paired_tx_id
        if tx_id is None:
            return [self.entity_id]
        leader = _resolve_entity_id(self.hass, _source_unique_id(self._entry_id, tx_id))
        if leader is None:
            return [self.entity_id]
        return [leader, *_zone_entity_ids_for_source(self.hass, self.coordinator, self._entry_id, tx_id)]

    async def async_join_players(self, group_members: list[str]) -> None:
        """Add the given zones to this zone's group (route them to its source)."""
        tx_id = self._zone.paired_tx_id
        if tx_id is None:
            raise HomeAssistantError(
                f"{self.name} has no source selected to group other zones into"
            )
        await _route_zone_entities(self.hass, self.coordinator, self._entry_id, group_members, tx_id)

    async def async_unjoin_player(self) -> None:
        """Leave the group: unpair just this zone (source -> None)."""
        await self.coordinator.client.async_select_source(self._group_id, None)
        await self.coordinator.async_request_refresh()


class BinaryMoIPSourceMediaPlayer(
    CoordinatorEntity[BinaryMoIPDataUpdateCoordinator], MediaPlayerEntity
):
    """A grouping-only media_player representing a single MoIP source (``group_tx``).

    Source-first session control: the source is the group leader, and joining a
    zone to it routes that zone to the source. It has no transport or volume yet
    (a later task) — only HA's GROUPING interface.
    """

    _attr_has_entity_name = False
    _attr_supported_features = SOURCE_SUPPORTED_FEATURES

    def __init__(
        self,
        coordinator: BinaryMoIPDataUpdateCoordinator,
        group_tx_id: int,
    ) -> None:
        """Initialize the source entity."""
        super().__init__(coordinator)
        self._group_tx_id = group_tx_id
        self._entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = _source_unique_id(self._entry_id, group_tx_id)

    async def async_added_to_hass(self) -> None:
        """Nudge peers to recompute group_members now that we're registered."""
        await super().async_added_to_hass()
        self.coordinator.async_update_listeners()

    @property
    def _source(self) -> MoIPSource:
        """Return the current MoIPSource for this entity from coordinator data."""
        return self.coordinator.data.sources[self._group_tx_id]

    @property
    def _options(self) -> dict:
        return self.coordinator.config_entry.options

    @property
    def available(self) -> bool:
        """Available when polling succeeds and the source still exists."""
        return super().available and self._group_tx_id in self.coordinator.data.sources

    @property
    def name(self) -> str:
        """Source name: options override, else the controller's input name.

        Reuses the same labeling (and collision disambiguation) as the zone
        source dropdowns, so a source reads identically in both places.
        """
        labels, _ = _build_source_maps(self.coordinator.data, self._options)
        return labels.get(self._group_tx_id) or self._source.name

    @property
    def device_info(self) -> DeviceInfo:
        """One device per source, so each transmitter is area-assignable."""
        source = self._source
        unit = (
            self.coordinator.data.units.get(source.unit_id)
            if source.unit_id
            else None
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self.name,
            manufacturer=MANUFACTURER,
            model=unit.model if unit is not None else None,
        )

    @property
    def state(self) -> MediaPlayerState:
        """PLAYING when the source is streaming, otherwise IDLE (no transport)."""
        if self._source.state == STATE_STREAMING:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    # --- grouping (this source is the group leader) -------------------------

    @property
    def group_members(self) -> list[str]:
        """Leader first (this source), then every zone currently routed to it."""
        return [
            self.entity_id,
            *_zone_entity_ids_for_source(
                self.hass, self.coordinator, self._entry_id, self._group_tx_id
            ),
        ]

    async def async_join_players(self, group_members: list[str]) -> None:
        """Route each given zone to this source (add them to the group)."""
        await _route_zone_entities(
            self.hass, self.coordinator, self._entry_id, group_members, self._group_tx_id
        )

    async def async_unjoin_player(self) -> None:
        """Disband: unpair every zone currently routed to this source."""
        members = _zone_entity_ids_for_source(
            self.hass, self.coordinator, self._entry_id, self._group_tx_id
        )
        await _route_zone_entities(
            self.hass, self.coordinator, self._entry_id, members, None
        )
