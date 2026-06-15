"""Tests for Listening Space level interpolation (spec §5/§11)."""

from __future__ import annotations

import pytest

from custom_components.binary_moip.const import (
    DEFAULT_MASTER_POSITIONS as POS,
    LEVEL_BACKGROUND as BG,
    LEVEL_LISTENING as LIST,
    LEVEL_PARTY as PARTY,
)
from custom_components.binary_moip.interpolation import (
    anchor_points,
    lerp,
    zone_target,
)


def test_lerp():
    assert lerp(0, 10, 0.0) == 0
    assert lerp(0, 10, 1.0) == 10
    assert lerp(10, 20, 0.5) == 15


def test_anchor_points_sorted_and_skips_missing():
    pts = anchor_points(POS, {PARTY: 80, BG: 10})  # listening missing
    assert pts == [(20.0, 10.0), (85.0, 80.0)]  # sorted by position
    # None values are dropped (graceful degradation)
    assert anchor_points(POS, {BG: 10, LIST: None, PARTY: 80}) == [
        (20.0, 10.0),
        (85.0, 80.0),
    ]


def test_no_anchors_returns_none():
    assert zone_target(50, []) is None


def test_one_anchor_is_constant():
    pts = anchor_points(POS, {LIST: 42})
    for m in (0, 20, 50, 85, 100):
        assert zone_target(m, pts) == 42


def test_three_anchor_presets_hit_exact_values():
    pts = anchor_points(POS, {BG: 10, LIST: 40, PARTY: 80})
    assert zone_target(POS[BG], pts) == 10
    assert zone_target(POS[LIST], pts) == 40
    assert zone_target(POS[PARTY], pts) == 80


def test_three_anchor_piecewise_midpoints():
    pts = anchor_points(POS, {BG: 10, LIST: 40, PARTY: 80})
    # halfway between background(20) and listening(50) -> 35 master -> mid value
    assert zone_target(35, pts) == pytest.approx((10 + 40) / 2)
    # halfway between listening(50) and party(85) -> 67.5 master
    assert zone_target(67.5, pts) == pytest.approx((40 + 80) / 2)


def test_clamp_outside_end_positions():
    pts = anchor_points(POS, {BG: 10, PARTY: 80})  # two anchors
    assert zone_target(0, pts) == 10  # below first -> first value
    assert zone_target(100, pts) == 80  # above last -> last value


def test_two_anchor_linear_between():
    pts = anchor_points(POS, {BG: 10, PARTY: 80})  # positions 20..85
    # midpoint of the 20..85 span is 52.5 -> midpoint value 45
    assert zone_target(52.5, pts) == pytest.approx(45)


def test_missing_middle_anchor_interpolates_across_neighbors():
    # Zone defines bg+party but not listening; at the listening preset (M=50)
    # it interpolates between its nearest defined anchors instead of failing.
    pts = anchor_points(POS, {BG: 10, PARTY: 80})
    val = zone_target(POS[LIST], pts)
    # 50 is (50-20)/(85-20) = 0.4615 of the way from 10 to 80
    assert val == pytest.approx(10 + (80 - 10) * (50 - 20) / (85 - 20))


def test_device_bounds_clamp():
    pts = anchor_points(POS, {BG: 10, LIST: 40, PARTY: 80})
    assert zone_target(POS[PARTY], pts, vmin=0.0, vmax=50.0) == 50.0  # capped
    assert zone_target(POS[BG], pts, vmin=20.0, vmax=100.0) == 20.0  # floored
