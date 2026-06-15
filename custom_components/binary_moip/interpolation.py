"""Level → per-zone volume interpolation for Listening Spaces (spec §5).

Pure functions, deliberately free of Home Assistant imports so they unit-test in
isolation (mirrors the card's logic.ts discipline). A *master* position on the
0–100 scale maps, per zone, to a native volume value by piecewise-linear
interpolation between that zone's *defined* anchors.
"""

from __future__ import annotations

from .const import LEVELS

# A defined anchor as (master_position, native_value).
AnchorPoint = tuple[float, float]


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolate from a to b at fraction t (t need not be clamped)."""
    return a + (b - a) * t


def anchor_points(
    master_positions: dict[str, float],
    zone_levels: dict[str, float | None],
) -> list[AnchorPoint]:
    """Build sorted (position, value) points for a zone's DEFINED anchors.

    ``master_positions`` maps level -> master-scale position (e.g. listening=50).
    ``zone_levels`` maps level -> native value; a level may be missing or None
    (graceful degradation, spec §5). Only levels with both a position and a
    value contribute a point. Result is sorted by position.
    """
    pts: list[AnchorPoint] = []
    for level in LEVELS:
        value = zone_levels.get(level)
        pos = master_positions.get(level)
        if value is not None and pos is not None:
            pts.append((float(pos), float(value)))
    pts.sort(key=lambda p: p[0])
    return pts


def zone_target(
    master: float,
    points: list[AnchorPoint],
    vmin: float | None = None,
    vmax: float | None = None,
) -> float | None:
    """Interpolate a zone's native value at master position M (spec §5).

    ``points`` are the zone's defined anchors (from :func:`anchor_points`).
    Graceful degradation by point count:
      * 0 -> ``None`` (zone has no calibrated value)
      * 1 -> constant at all M
      * 2 -> linear between, clamped outside the two positions
      * 3 -> piecewise linear, clamped outside the end positions
    The result is clamped to ``[vmin, vmax]`` when provided (device bounds).
    """
    if not points:
        return None

    if len(points) == 1:
        val = points[0][1]
    elif master <= points[0][0]:
        val = points[0][1]
    elif master >= points[-1][0]:
        val = points[-1][1]
    else:
        # Find the bracketing segment and interpolate within it.
        val = points[-1][1]
        for (p0, v0), (p1, v1) in zip(points, points[1:]):
            if p0 <= master <= p1:
                t = 0.0 if p1 == p0 else (master - p0) / (p1 - p0)
                val = lerp(v0, v1, t)
                break

    if vmin is not None:
        val = max(vmin, val)
    if vmax is not None:
        val = min(vmax, val)
    return val
