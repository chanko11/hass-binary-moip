"""Listening Space model, storage, and runtime manager (spec §4–§6).

A Space is a named, calibrated group of zones. Definitions + per-zone driver
config persist in a dedicated ``Store`` (separate from config-entry options).
The manager turns a single master position into per-zone native volumes via
:mod:`.interpolation` and drives each zone through its :class:`.drivers.ZoneDriver`.
Routing is always MoIP (``paired_tx``); the driver handles power/input/volume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_PINK_MEDIA,
    CONF_PINK_PLAYER,
    CONF_PINK_TYPE,
    DEFAULT_MASTER_POSITIONS,
    DOMAIN,
    LEVEL_LISTENING,
    LEVELS,
    MASTER_MAX,
    MASTER_MIN,
    OPT_BACKING,
    OPT_CALIBRATION,
    OPT_SOURCES,
    OPT_SPACES,
    OPT_ZONE_CONFIGS,
    REF_AUTO,
    REF_PINK,
    ROUTING_MOIP,
    SAFE_DEFAULT_FRACTION,
    SPACES_STORAGE_KEY,
    SPACES_STORAGE_VERSION,
    ZONE_NUDGE,
    ZONE_OFF,
    ZONE_ON,
)
from .coordinator import BinaryMoIPDataUpdateCoordinator
from .drivers import ZoneDriver, build_driver
from .interpolation import anchor_points, zone_target

_LOGGER = logging.getLogger(__name__)


@dataclass
class Space:
    """A calibrated group of zones with three per-zone level anchors."""

    id: str
    name: str
    # Effective member zones (group_rx ids) used at runtime. When the Space is
    # label-sourced, the manager fills this by resolving the label at load.
    zones: list[int] = field(default_factory=list)
    # --- membership source (authored config) ---
    # Optional HA label id; when set, membership = label->areas->zones (+/- overrides).
    label: str | None = None
    # Manual overrides on top of the resolved (or explicit) set.
    zones_add: list[int] = field(default_factory=list)
    zones_exclude: list[int] = field(default_factory=list)
    # level -> {group_id: native value}; anchors may be partial (graceful degr.)
    levels: dict[str, dict[int, float]] = field(default_factory=dict)
    # level -> master-scale position (defaults to DEFAULT_MASTER_POSITIONS)
    master_positions: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_MASTER_POSITIONS)
    )

    @classmethod
    def from_dict(cls, d: dict) -> "Space":
        # JSON object keys are strings; group ids are ints internally.
        levels = {
            lvl: {int(gid): float(v) for gid, v in (d.get("levels", {}).get(lvl, {}) or {}).items()}
            for lvl in LEVELS
        }
        positions = dict(DEFAULT_MASTER_POSITIONS)
        positions.update({k: float(v) for k, v in (d.get("master_positions") or {}).items()})
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            zones=[int(z) for z in d.get("zones", [])],
            label=d.get("label") or None,
            zones_add=[int(z) for z in d.get("zones_add", [])],
            zones_exclude=[int(z) for z in d.get("zones_exclude", [])],
            levels=levels,
            master_positions=positions,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "zones": list(self.zones),
            "label": self.label,
            "zones_add": list(self.zones_add),
            "zones_exclude": list(self.zones_exclude),
            "levels": {
                lvl: {str(gid): v for gid, v in self.levels.get(lvl, {}).items()}
                for lvl in LEVELS
            },
            "master_positions": dict(self.master_positions),
        }

    def zone_levels(self, gid: int) -> dict[str, float | None]:
        """The {level: value|None} for one zone across all anchors."""
        return {lvl: self.levels.get(lvl, {}).get(gid) for lvl in LEVELS}

    def nearest_level(self, master: float) -> str | None:
        """The preset level whose position is closest to ``master`` (or None)."""
        if not self.master_positions:
            return None
        return min(self.master_positions, key=lambda l: abs(self.master_positions[l] - master))


@dataclass
class RuntimeState:
    """Live state for an active Space (not persisted; resets on restart)."""

    master: float
    source: str | None = None
    level: str | None = None
    tx_id: int | None = None  # resolved MoIP source routed (for zone_set re-route)


def clamp_master(value: float) -> float:
    return max(MASTER_MIN, min(MASTER_MAX, float(value)))


class SpaceManager:
    """Owns Space definitions, drivers, storage, and live activation."""

    def __init__(
        self, hass: HomeAssistant, coordinator: BinaryMoIPDataUpdateCoordinator
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._store: Store = Store(hass, SPACES_STORAGE_VERSION, SPACES_STORAGE_KEY)
        self.spaces: dict[str, Space] = {}
        # Per-zone {routing, driver{...}} structural config (from options).
        self.zone_configs: dict[int, dict] = {}
        self._drivers: dict[int, ZoneDriver] = {}
        # Runtime
        self.active: dict[str, RuntimeState] = {}
        self.zone_owner: dict[int, str] = {}  # group_id -> active space id (overlap)

    # --- storage -------------------------------------------------------------

    async def async_load(self) -> None:
        """Load structural config from config-entry options and calibration
        anchor VALUES from the dedicated Store, merged into Space objects.
        """
        options = getattr(self.coordinator.config_entry, "options", {}) or {}
        self.zone_configs = {
            int(gid): cfg for gid, cfg in (options.get(OPT_ZONE_CONFIGS) or {}).items()
        }
        store = await self._store.async_load() or {}
        anchors = store.get("anchors", {})  # {space_id: {level: {gid_str: value}}}

        self.spaces = {}
        for struct in options.get(OPT_SPACES) or []:
            space = Space.from_dict(struct)  # name/positions/source (no anchors here)
            sa = anchors.get(space.id, {})
            space.levels = {
                lvl: {int(g): float(v) for g, v in (sa.get(lvl) or {}).items()}
                for lvl in LEVELS
            }
            # Resolve effective membership: from the label (if set) else the
            # explicit list, then apply add/exclude overrides.
            explicit = list(space.zones)
            space.zones = self._resolve_membership(
                space.label, explicit, space.zones_add, space.zones_exclude
            )
            self.spaces[space.id] = space
        self._drivers.clear()

    def _resolve_membership(
        self,
        label: str | None,
        explicit: list[int],
        add: list[int],
        exclude: list[int],
    ) -> list[int]:
        """Effective member zones: label->areas->zones (or explicit), +add -exclude."""
        base = self._zones_for_label(label) if label else list(explicit)
        excl = set(exclude or [])
        eff = [g for g in base if g not in excl]
        for g in add or []:
            if g not in eff and g not in excl:
                eff.append(g)
        return eff

    def _zones_for_label(self, label_id: str) -> list[int]:
        """MoIP zones (group ids) whose HA area carries ``label_id``.

        Resolves label -> areas-with-that-label -> the binary_moip zone
        media_players assigned to those areas. Disabled/unassigned zones and
        rooms without speakers contribute nothing (spec §4, label scheme doc).
        """
        from homeassistant.helpers import (
            area_registry,
            device_registry,
            entity_registry,
        )

        areg = area_registry.async_get(self.hass)
        ereg = entity_registry.async_get(self.hass)
        dreg = device_registry.async_get(self.hass)
        entry_id = self.coordinator.config_entry.entry_id
        area_ids = {a.id for a in areg.async_list_areas() if label_id in (a.labels or set())}

        out: list[int] = []
        for gid in self.coordinator.data.zones:
            eid = ereg.async_get_entity_id("media_player", DOMAIN, f"{entry_id}_{gid}")
            if not eid:
                continue
            ent = ereg.async_get(eid)
            if ent is None or ent.disabled_by is not None:
                continue
            aid = ent.area_id
            if aid is None and ent.device_id:
                dev = dreg.async_get(ent.device_id)
                aid = dev.area_id if dev else None
            if aid in area_ids:
                out.append(gid)
        return out

    async def async_save_anchors(self) -> None:
        """Persist calibration anchor values to the Store (used by Phase 2)."""
        await self._store.async_save(
            {
                "anchors": {
                    sid: {
                        lvl: {str(g): v for g, v in s.levels.get(lvl, {}).items()}
                        for lvl in LEVELS
                    }
                    for sid, s in self.spaces.items()
                }
            }
        )

    # --- helpers -------------------------------------------------------------

    def _driver(self, gid: int) -> ZoneDriver:
        if gid not in self._drivers:
            cfg = self.zone_configs.get(gid) or {}
            self._drivers[gid] = build_driver(
                self.hass, self.coordinator, gid, cfg.get("driver")
            )
        return self._drivers[gid]

    def _zone_routing(self, gid: int) -> str:
        """How this zone gets its audio: ``moip`` (default) or ``device``."""
        return (self.zone_configs.get(gid) or {}).get("routing", ROUTING_MOIP)

    def _calibration_config(self) -> dict:
        options = getattr(self.coordinator.config_entry, "options", {}) or {}
        return options.get(OPT_CALIBRATION) or {}

    def _source_backing(self, tx_id: int | None) -> str | None:
        """The backing media_player entity_id configured for a MoIP source."""
        if tx_id is None:
            return None
        options = getattr(self.coordinator.config_entry, "options", {}) or {}
        return (options.get(OPT_SOURCES, {}).get(str(tx_id), {}) or {}).get(OPT_BACKING)

    def _source_for_backing(self, player: str) -> int | None:
        """The MoIP source (tx id) whose backing media_player is ``player``."""
        options = getattr(self.coordinator.config_entry, "options", {}) or {}
        for tx, cfg in (options.get(OPT_SOURCES, {}) or {}).items():
            if (cfg or {}).get(OPT_BACKING) == player:
                return int(tx)
        return None

    def get_space(self, space_id: str) -> Space:
        space = self.spaces.get(space_id)
        if space is None:
            raise KeyError(f"Unknown Listening Space '{space_id}'")
        return space

    def _resolve_source_tx(self, source: str | int | None) -> int | None:
        """Resolve a source to a group_tx id. Accepts a tx id (int), a source
        media_player entity_id (``..._source_<tx>`` unique_id), or None (no route
        change).
        """
        if source is None:
            return None
        if isinstance(source, int):
            return source
        if isinstance(source, str) and source.isdigit():
            return int(source)
        # entity_id -> unique_id "<entry>_source_<tx>"
        ent = er.async_get(self.hass).async_get(source)
        if ent and ent.unique_id and "_source_" in ent.unique_id:
            try:
                return int(ent.unique_id.rsplit("_source_", 1)[1])
            except ValueError:
                pass
        raise ValueError(f"Could not resolve source '{source}' to a MoIP source")

    async def _route(self, gid: int, tx_id: int | None) -> None:
        """Set (or clear) the MoIP source routed to a zone."""
        await self.coordinator.client.async_select_source(gid, tx_id)

    async def _apply_levels(self, space: Space, master: float) -> None:
        """Compute + push per-zone native volumes for zones this space owns.

        Uncalibrated zones (no anchors) get a safe low default rather than being
        skipped or blasted to full (spec §4/§5).
        """
        for gid in space.zones:
            if self.zone_owner.get(gid) != space.id:
                continue  # stolen by another active space
            driver = self._driver(gid)
            lo, hi = driver.bounds
            target = zone_target(
                master, anchor_points(space.master_positions, space.zone_levels(gid)), lo, hi
            )
            if target is None and lo is not None and hi is not None:
                target = lo + SAFE_DEFAULT_FRACTION * (hi - lo)  # safe default
            if target is not None:
                await driver.set_level(target)

    # --- playback API (spec §6) ---------------------------------------------

    async def async_activate(
        self,
        space_id: str,
        *,
        level: str | None = None,
        master: float | None = None,
        source: str | int | None = None,
    ) -> None:
        space = self.get_space(space_id)
        if master is not None:
            pos = clamp_master(master)
        elif level is not None:
            pos = clamp_master(space.master_positions.get(level, DEFAULT_MASTER_POSITIONS.get(level, 50.0)))
        else:
            pos = clamp_master(space.master_positions.get("listening", 50.0))

        tx_id = self._resolve_source_tx(source)

        # Disallow overlap: claim every zone, stealing from any other active space.
        for gid in space.zones:
            prev = self.zone_owner.get(gid)
            if prev and prev != space_id:
                _LOGGER.debug("Space %s steals zone %s from %s", space_id, gid, prev)
            self.zone_owner[gid] = space_id

        for gid in space.zones:
            # MoIP-routed zones get the source via the controller; device-routed
            # zones source their own content (handled by their driver).
            if tx_id is not None and self._zone_routing(gid) == ROUTING_MOIP:
                await self._route(gid, tx_id)
            await self._driver(gid).activate()

        self.active[space_id] = RuntimeState(
            master=pos,
            source=(source if isinstance(source, str) else None),
            level=level,
            tx_id=tx_id,
        )
        await self._apply_levels(space, pos)
        await self.coordinator.async_request_refresh()

    async def async_deactivate(self, space_id: str) -> None:
        space = self.get_space(space_id)
        for gid in list(space.zones):
            if self.zone_owner.get(gid) != space_id:
                continue
            driver = self._driver(gid)
            await driver.deactivate()
            if self._zone_routing(gid) == ROUTING_MOIP:
                await self._route(gid, None)  # unpair the MoIP route
            self.zone_owner.pop(gid, None)
        self.active.pop(space_id, None)
        await self.coordinator.async_request_refresh()

    async def async_set_master(self, space_id: str, position: float) -> None:
        space = self.get_space(space_id)
        state = self.active.get(space_id)
        if state is None:
            raise RuntimeError(f"Listening Space '{space_id}' is not active")
        state.master = clamp_master(position)
        state.level = space.nearest_level(state.master)
        await self._apply_levels(space, state.master)
        await self.coordinator.async_request_refresh()

    async def async_set_level(self, space_id: str, level: str) -> None:
        space = self.get_space(space_id)
        if level not in LEVELS:
            raise ValueError(f"Unknown level '{level}'")
        pos = clamp_master(space.master_positions.get(level, 50.0))
        if space_id in self.active:
            await self.async_set_master(space_id, pos)
        else:
            await self.async_activate(space_id, level=level)

    # --- calibration API (spec §6) ------------------------------------------

    async def async_set_anchor(
        self, space_id: str, zone: int, level: str, value: float | None = None
    ) -> float:
        """Save a zone's calibration anchor for a level. With no value, snapshot
        the zone's *current live* native volume (the calibration-by-ear flow).
        Persists to the Store. Returns the stored value.
        """
        space = self.get_space(space_id)
        if level not in LEVELS:
            raise ValueError(f"Unknown level '{level}'")
        if zone not in space.zones:
            raise ValueError(f"Zone {zone} is not in Listening Space '{space_id}'")
        if value is None:
            value = self._driver(zone).current_native()
            if value is None:
                raise RuntimeError(f"No current volume to snapshot for zone {zone}")
        space.levels.setdefault(level, {})[zone] = float(value)
        await self.async_save_anchors()
        return float(value)

    async def async_clear_anchor(self, space_id: str, zone: int, level: str) -> None:
        """Remove a zone's anchor for a level (re-calibration). Persists."""
        space = self.get_space(space_id)
        space.levels.get(level, {}).pop(zone, None)
        await self.async_save_anchors()

    async def async_calibration_play(
        self,
        space_id: str,
        *,
        ref_type: str = REF_AUTO,
        source: str | int | None = None,
        sample: str | None = None,
        level: str | None = None,
    ) -> None:
        """Audition a reference across a Space's zones so the walk-around
        adjust→snapshot loop works (spec §6/§7).

        ``pink`` mode plays the configured Local-Media pink-noise item (looped)
        on its MA player, routed to the zones. ``sample`` mode routes ``source``
        (or whatever's already routed) and optionally ``play_media``s a sample on
        that source's backing player — so calibration isn't blocked on the
        pink-noise file. ``auto`` picks pink when configured, else sample.
        """
        space = self.get_space(space_id)
        cfg = self._calibration_config()
        pink_player = cfg.get(CONF_PINK_PLAYER)
        pink_media = cfg.get(CONF_PINK_MEDIA)
        use_pink = (ref_type == REF_PINK) or (
            ref_type == REF_AUTO and pink_player and pink_media
        )
        if ref_type == REF_PINK and not (pink_player and pink_media):
            raise RuntimeError("No pink-noise reference configured (Calibration options)")

        # Resolve the MoIP source to route. For pink, default to the source whose
        # backing player is the configured pink player.
        route_source: str | int | None = source
        if use_pink and route_source is None and pink_player:
            route_source = self._source_for_backing(pink_player)
        if route_source is None and space_id not in self.active:
            raise RuntimeError(
                "Provide a source (or start one playing) to audition for calibration"
            )

        # Route + set working levels (reuses activation; uncalibrated -> safe default).
        await self.async_activate(
            space_id, level=level or LEVEL_LISTENING, source=route_source
        )

        if use_pink:
            await self.hass.services.async_call(
                "media_player",
                "repeat_set",
                {"entity_id": pink_player, "repeat": "all"},
                blocking=True,
            )
            await self.hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": pink_player,
                    "media_content_id": pink_media,
                    "media_content_type": cfg.get(CONF_PINK_TYPE) or "music",
                    "enqueue": "replace",
                },
                blocking=True,
            )
        elif sample:
            backing = self._source_backing(self._resolve_source_tx(route_source))
            if backing:
                await self.hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": backing,
                        "media_content_id": sample,
                        "media_content_type": "music",
                        "enqueue": "replace",
                    },
                    blocking=True,
                )

    async def async_zone_set(
        self, space_id: str, zone: int, action: str, delta: float | None = None
    ) -> None:
        """Per-zone override within an active Space (spec §6).

        ``on`` (re)claims + routes + sets the zone to the current interpolated
        level; ``off`` releases it (unroute / device deactivate); ``nudge``
        applies a temporary ±``delta`` to the zone's live native volume (it's
        overwritten on the next preset tap or master move).
        """
        space = self.get_space(space_id)
        if zone not in space.zones:
            raise ValueError(f"Zone {zone} is not in Listening Space '{space_id}'")
        state = self.active.get(space_id)
        driver = self._driver(zone)

        if action == ZONE_ON:
            self.zone_owner[zone] = space_id
            if state and state.tx_id is not None and self._zone_routing(zone) == ROUTING_MOIP:
                await self._route(zone, state.tx_id)
            await driver.activate()
            master = state.master if state else space.master_positions.get(LEVEL_LISTENING, 50.0)
            lo, hi = driver.bounds
            target = zone_target(
                master, anchor_points(space.master_positions, space.zone_levels(zone)), lo, hi
            )
            if target is None and lo is not None and hi is not None:
                target = lo + SAFE_DEFAULT_FRACTION * (hi - lo)
            if target is not None:
                await driver.set_level(target)
        elif action == ZONE_OFF:
            await driver.deactivate()
            if self._zone_routing(zone) == ROUTING_MOIP:
                await self._route(zone, None)
            self.zone_owner.pop(zone, None)
        elif action == ZONE_NUDGE:
            if delta is None:
                raise ValueError("nudge requires a delta")
            cur = driver.current_native()
            if cur is None:
                raise RuntimeError(f"No current volume to nudge for zone {zone}")
            lo, hi = driver.bounds
            new = cur + float(delta)
            if lo is not None:
                new = max(lo, new)
            if hi is not None:
                new = min(hi, new)
            await driver.set_level(new)
        else:
            raise ValueError(f"Unknown zone action '{action}'")

        await self.coordinator.async_request_refresh()
