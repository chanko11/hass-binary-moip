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
    DEFAULT_MASTER_POSITIONS,
    LEVELS,
    MASTER_MAX,
    MASTER_MIN,
    OPT_SPACES,
    OPT_ZONE_CONFIGS,
    ROUTING_MOIP,
    SPACES_STORAGE_KEY,
    SPACES_STORAGE_VERSION,
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
    zones: list[int] = field(default_factory=list)  # group_rx ids, ordered
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
            levels=levels,
            master_positions=positions,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "zones": list(self.zones),
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
            space = Space.from_dict(struct)  # name/zones/positions (no anchors here)
            sa = anchors.get(space.id, {})
            space.levels = {
                lvl: {int(g): float(v) for g, v in (sa.get(lvl) or {}).items()}
                for lvl in LEVELS
            }
            self.spaces[space.id] = space
        self._drivers.clear()

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
        """Compute + push per-zone native volumes for zones this space owns."""
        for gid in space.zones:
            if self.zone_owner.get(gid) != space.id:
                continue  # stolen by another active space
            driver = self._driver(gid)
            lo, hi = driver.bounds
            target = zone_target(
                master, anchor_points(space.master_positions, space.zone_levels(gid)), lo, hi
            )
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
            master=pos, source=(source if isinstance(source, str) else None), level=level
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
