"""Tests for GPS-based direction inference in `real_normalization`.

Lock the invariant that the compiler reproduces the same N/E/S/W bucketing as
Service-ai's GPI standardizer (`Service-ai/src/preprocessing/standardizer.py`)
across the three input regimes we have to support:

1. Snapshot carries cross GPS + road polylines (the production target).
2. Snapshot carries only legacy `from_cross_direction` codes in the 4-direction
   encoding 1..4 (older deployments).
3. Snapshot carries only legacy codes in the 8-direction encoding 0/2/4/6
   (the convention seen in real `v_road` dumps).
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import pytest

from src.ops.real_normalization import (
    RealRoad,
    _angle_to_compass,
    _build_direction_map,
    _candidates_per_direction,
    _detect_legacy_direction_encoding,
    _enu_offset,
    _LEGACY_DIRECTION_CODE_4DIR,
    _LEGACY_DIRECTION_CODE_8DIR,
)


# --- low-level helpers --------------------------------------------------------

@pytest.mark.parametrize("angle,expected", [
    (270.0, "N"), (180.0, "E"), (90.0, "S"), (0.0, "W"),
    # Bucket boundaries: lower-inclusive
    (225.0, "N"), (135.0, "E"), (45.0, "S"), (315.0, "W"),
    # Just-below boundaries land in the previous quadrant
    (224.9, "E"), (134.9, "S"), (44.9, "W"), (314.9, "N"),
    # Wrap around for W
    (359.9, "W"), (0.1, "W"),
])
def test_angle_to_compass_matches_gpi_cutoffs(angle, expected):
    # Must match Service-ai/src/preprocessing/standardizer.py:149-156 exactly.
    assert _angle_to_compass(angle) == expected


def test_enu_offset_is_locally_planar():
    # ~50N: 1deg latitude is much longer than 1deg longitude.
    ref = (50.928, 6.92)
    dx_e, dy_n = _enu_offset(ref, (50.928, 6.921))  # 1e-3 deg lon east
    assert dx_e == pytest.approx(70.1, abs=0.2)
    assert dy_n == pytest.approx(0.0, abs=0.05)

    dx_e, dy_n = _enu_offset(ref, (50.929, 6.92))  # 1e-3 deg lat north
    assert dx_e == pytest.approx(0.0, abs=0.05)
    assert dy_n == pytest.approx(111.2, abs=0.2)


# --- encoding auto-detection --------------------------------------------------

def _road(rid: int, from_cross: int, from_dir: int, to_cross=None, to_dir=None) -> RealRoad:
    return RealRoad(
        id=rid, from_cross=from_cross, to_cross=to_cross,
        from_dir=from_dir, to_dir=to_dir,
        lanes=2, length_m=100.0, speed_design=50.0, capacity_design=1800.0,
    )


def test_detect_4dir_when_all_values_in_1_to_4():
    roads = [_road(1, 100, 1), _road(2, 100, 2), _road(3, 100, 3), _road(4, 100, 4)]
    assert _detect_legacy_direction_encoding(roads) is _LEGACY_DIRECTION_CODE_4DIR


def test_detect_8dir_when_any_value_outside_1_to_4():
    roads = [_road(1, 100, 0), _road(2, 100, 2), _road(3, 100, 4), _road(4, 100, 6)]
    assert _detect_legacy_direction_encoding(roads) is _LEGACY_DIRECTION_CODE_8DIR


def test_detect_falls_back_to_4dir_for_empty_input():
    # No directional information at all - either table is acceptable since the
    # caller will get None back for every lookup.
    assert _detect_legacy_direction_encoding([]) is _LEGACY_DIRECTION_CODE_4DIR


# --- end-to-end direction inference on a synthetic 4-way junction -------------

def _gen_offset(center: Tuple[float, float], compass: str, dist_m: float = 150.0) -> Tuple[float, float]:
    lat0, lon0 = center
    earth_r = 6371000.0
    dlat = (dist_m / earth_r) * (180.0 / math.pi)
    dlon = (dist_m / (earth_r * math.cos(math.radians(lat0)))) * (180.0 / math.pi)
    offsets = {"N": (lat0 + dlat, lon0), "S": (lat0 - dlat, lon0),
               "E": (lat0, lon0 + dlon), "W": (lat0, lon0 - dlon)}
    return offsets[compass]


def _make_four_way(center: Tuple[float, float], *, with_polyline: bool, legacy_codes: Dict[str, int] | None):
    """Build a 4-way intersection where each road approaches from one compass side."""
    roads: List[RealRoad] = []
    for idx, compass in enumerate("NESW"):
        far = _gen_offset(center, compass)
        # External approach: from_cross is this junction (id=100), to_cross None.
        legacy_code = legacy_codes[compass] if legacy_codes else None
        r = RealRoad(
            id=100 + idx, from_cross=100, to_cross=None,
            from_dir=legacy_code, to_dir=None,
            lanes=2, length_m=150.0, speed_design=50.0, capacity_design=1800.0,
        )
        if with_polyline:
            # order_number=1 at far point, order_number=2 at junction stop line.
            r.coordinates = [far, center]
        roads.append(r)
    return roads


def test_gps_driven_recovers_all_four_directions():
    center = (50.928, 6.92)
    roads = _make_four_way(center, with_polyline=True, legacy_codes=None)
    centers = {100: center}
    encoding = _detect_legacy_direction_encoding(roads)
    cands = _candidates_per_direction(roads, 100, centers, encoding)
    chosen = _build_direction_map(cands)
    # 4 cardinals, one road each, deterministic.
    assert sorted(chosen.values()) == [0, 1, 2, 3]
    by_dir = {v: int(k) for k, v in chosen.items()}
    assert by_dir == {0: 100, 1: 101, 2: 102, 3: 103}


def test_4dir_legacy_fallback_when_no_gps():
    center = (50.928, 6.92)
    legacy = {"N": 1, "E": 2, "S": 3, "W": 4}
    roads = _make_four_way(center, with_polyline=False, legacy_codes=legacy)
    encoding = _detect_legacy_direction_encoding(roads)
    assert encoding is _LEGACY_DIRECTION_CODE_4DIR
    cands = _candidates_per_direction(roads, 100, {}, encoding)
    chosen = _build_direction_map(cands)
    by_dir = {v: int(k) for k, v in chosen.items()}
    assert by_dir == {0: 100, 1: 101, 2: 102, 3: 103}


def test_8dir_legacy_fallback_when_no_gps():
    center = (50.928, 6.92)
    legacy = {"N": 0, "E": 2, "S": 4, "W": 6}
    roads = _make_four_way(center, with_polyline=False, legacy_codes=legacy)
    encoding = _detect_legacy_direction_encoding(roads)
    assert encoding is _LEGACY_DIRECTION_CODE_8DIR
    cands = _candidates_per_direction(roads, 100, {}, encoding)
    chosen = _build_direction_map(cands)
    by_dir = {v: int(k) for k, v in chosen.items()}
    assert by_dir == {0: 100, 1: 101, 2: 102, 3: 103}


def test_diagonal_legacy_codes_are_dropped_not_misbucketed():
    # Encoding is detected as 8-dir because of value 5 (SW). A purely diagonal
    # code must drop out - we'd rather composer raise than route the road into
    # the wrong cardinal bucket.
    center = (50.928, 6.92)
    legacy = {"N": 0, "E": 2, "S": 4, "W": 5}  # 5 is SW, not a cardinal
    roads = _make_four_way(center, with_polyline=False, legacy_codes=legacy)
    encoding = _detect_legacy_direction_encoding(roads)
    assert encoding is _LEGACY_DIRECTION_CODE_8DIR
    cands = _candidates_per_direction(roads, 100, {}, encoding)
    chosen = _build_direction_map(cands)
    # W slot stays empty so composer can detect the gap; the other three resolve.
    assert sorted(chosen.values()) == [0, 1, 2]


# --- collision tiebreaker -----------------------------------------------------

def test_closest_to_ideal_wins_when_two_roads_share_bucket():
    """Two roads both bucket to E; the one with angle nearer 180° wins.

    Mirrors Service-ai standardizer.py:222-227.
    """
    center = (50.928, 6.92)
    earth_r = 6371000.0

    # Synthesize a "far point" such that the vector INTO the junction has a
    # specific angle in [0, 360). vector = stop - prev. With stop = center and
    # prev = far, we want (center - far) to have angle theta -> far = center -
    # 150m * (cos(theta), sin(theta)) in local ENU.
    def far_for_angle(theta_deg: float):
        d = 150.0
        # local ENU offsets from center (dx_east, dy_north)
        dx = -d * math.cos(math.radians(theta_deg))
        dy = -d * math.sin(math.radians(theta_deg))
        # invert ENU -> lat/lon
        dlat = (dy / earth_r) * (180.0 / math.pi)
        dlon = (dx / (earth_r * math.cos(math.radians(center[0])))) * (180.0 / math.pi)
        return (center[0] + dlat, center[1] + dlon)

    far_a = far_for_angle(175.0)  # very close to ideal E=180
    far_b = far_for_angle(145.0)  # still in E bucket [135, 225)
    road_a = RealRoad(id=200, from_cross=100, to_cross=None, from_dir=None, to_dir=None,
                     lanes=2, length_m=200, speed_design=50, capacity_design=1800)
    road_a.coordinates = [far_a, center]
    road_b = RealRoad(id=201, from_cross=100, to_cross=None, from_dir=None, to_dir=None,
                     lanes=2, length_m=200, speed_design=50, capacity_design=1800)
    road_b.coordinates = [far_b, center]

    cands = _candidates_per_direction([road_a, road_b], 100, {100: center}, _LEGACY_DIRECTION_CODE_4DIR)
    # Both should land in E.
    assert len(cands["E"]) == 2
    chosen = _build_direction_map(cands)
    # Closest-to-ideal wins (road A at 175° vs road B at 145°, ideal = 180°).
    assert chosen == {"200": 1}


def test_internal_roads_are_classified_at_both_endpoints():
    # An internal road linking two junctions A (west) and B (east): at A the
    # vector points east-bound INTO A from the east side, so it buckets to E
    # at A and to W at B. (Wait - vector points INTO the junction, so at A the
    # far end is B, and vector = A_center - B_center points west; that buckets
    # to W. At B the symmetric vector buckets to E.)
    center_a = (50.928, 6.918)  # west junction
    center_b = (50.928, 6.922)  # east junction
    road = RealRoad(id=300, from_cross=10, to_cross=20, from_dir=None, to_dir=None,
                   lanes=2, length_m=200, speed_design=50, capacity_design=1800)
    road.coordinates = [center_a, center_b]

    centers = {10: center_a, 20: center_b}
    # At A: nearest endpoint to A is center_a (the first); prev is center_b.
    # vec = center_a - center_b = (negative east), angle ~ 180 -> E (vector points INTO A from east side).
    cands_a = _candidates_per_direction([road], 10, centers, _LEGACY_DIRECTION_CODE_4DIR)
    chosen_a = _build_direction_map(cands_a)
    # At B: nearest endpoint is center_b; prev is center_a.
    # vec = center_b - center_a = (positive east), angle ~ 0 -> W.
    cands_b = _candidates_per_direction([road], 20, centers, _LEGACY_DIRECTION_CODE_4DIR)
    chosen_b = _build_direction_map(cands_b)

    assert chosen_a == {"300": 1}  # E at A
    assert chosen_b == {"300": 3}  # W at B
