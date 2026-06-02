"""DB-driven real normalization compiler.

Mục tiêu:
- Pull thông tin thực tế từ `real_network_snapshot` (service-owned DB) hoặc
  fallback view management cũ (v_area_cross, v_cross, v_road, v_cycle, v_stage)
- Build network.json + per-cross config (direction_map, cycles, roads_static)
- Output `real_normalization.json` cho composer dùng khi build runtime bundle.

Compatibility check sim ↔ real đã chuyển sang
`src/ops/composer.build_deployment_map_from_real_normalization()` để gắn
chặt với pipeline compose. Module này chỉ lo sinh artifact real.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import bindparam, create_engine, text

from src.core.logger import logger
from src.preprocessing.feature_builder import (
    DEFAULT_ROAD_LANES,
    DEFAULT_ROAD_LENGTH_M,
    DEFAULT_ROAD_SATURATION_FLOW,
    DEFAULT_ROAD_SPEED_DESIGN_KMH,
)

MAX_NEIGHBORS = 4

# Direction encoding for direction_map output: 0=N, 1=E, 2=S, 3=W (must match composer._DIR_TO_IDX).
_NESW = ("N", "E", "S", "W")
_DIR_INDEX = {d: i for i, d in enumerate(_NESW)}

# GPI compass bucket boundaries (must match Service-ai/src/preprocessing/standardizer.py:149-156).
# angle is arctan2(dy, dx) of vector INTO junction, in degrees [0, 360).
#   N: 225..315 (vector points south-ish, road comes from north)
#   E: 135..225
#   S: 45..135
#   W: else (315..360 or 0..45)
_IDEAL_ANGLES = {"N": 270.0, "E": 180.0, "S": 90.0, "W": 0.0}

# Legacy from_cross_direction encodings:
#   - 4-direction (real_normalization v1):   1=N, 2=E, 3=S, 4=W
#   - 8-direction (v_road dump in HN data):  0=N, 2=E, 4=S, 6=W (1/3/5/7 = NE/SE/SW/NW)
# The two encodings collide on {1,2,3,4}: code 4 means W in 4-dir but S in 8-dir, etc.
# We pick the convention per snapshot by inspecting the value range (see
# `_detect_legacy_direction_encoding`) instead of guessing per row.
_LEGACY_DIRECTION_CODE_4DIR = {1: "N", 2: "E", 3: "S", 4: "W"}
_LEGACY_DIRECTION_CODE_8DIR = {0: "N", 2: "E", 4: "S", 6: "W"}


@dataclass
class RealRoad:
    id: int
    from_cross: Optional[int]
    to_cross: Optional[int]
    from_dir: Optional[int]
    to_dir: Optional[int]
    lanes: Optional[int]
    length_m: Optional[float]
    speed_design: Optional[float]
    capacity_design: Optional[float]
    # Polyline points sorted by v_road_coordinate.order_number (lat, lon).
    coordinates: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class RealCycle:
    id: int
    cross_id: int
    cycle_type: int
    cycle_length: Optional[int] = None
    cycle_name: Optional[str] = None
    created_date: Optional[str] = None
    yellow: Optional[int] = None
    red_clear: Optional[int] = None


@dataclass
class RealStage:
    id: int
    cycle_id: int
    order_number: int
    stage_code: Optional[str] = None
    old_id: Optional[str] = None
    green: Optional[int] = None
    yellow: Optional[int] = None
    red_clear: Optional[int] = None
    min_green_time: Optional[int] = None
    max_green_time: Optional[int] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_legacy_direction_encoding(roads: List[RealRoad]) -> Dict[int, str]:
    """Pick the right {code: compass} table given the codes actually used.

    If every observed direction value lies in {1, 2, 3, 4} we treat the snapshot
    as 4-direction (1=N, 2=E, 3=S, 4=W). Otherwise we assume 8-direction
    (0=N, 2=E, 4=S, 6=W; diagonals dropped). Both tables intentionally drop
    values that do not represent cardinal directions so the caller falls back
    to the GPS path or leaves the direction unset.
    """
    values: set = set()
    for road in roads:
        for v in (road.from_dir, road.to_dir):
            if v is None:
                continue
            try:
                values.add(int(v))
            except (TypeError, ValueError):
                continue

    if not values:
        return _LEGACY_DIRECTION_CODE_4DIR  # any table works for empty input
    if values <= {1, 2, 3, 4}:
        return _LEGACY_DIRECTION_CODE_4DIR
    return _LEGACY_DIRECTION_CODE_8DIR


def _legacy_direction_compass(db_direction: Optional[int], encoding: Dict[int, str]) -> Optional[str]:
    """Map a legacy direction code to N/E/S/W using the snapshot-wide encoding."""
    if db_direction is None:
        return None
    try:
        v = int(db_direction)
    except (TypeError, ValueError):
        return None
    return encoding.get(v)


def _enu_offset(p_ref: Tuple[float, float], p: Tuple[float, float]) -> Tuple[float, float]:
    """Local ENU offset (dx_east_m, dy_north_m) of `p` relative to `p_ref` (lat, lon).

    Uses spherical approximation: 1 degree latitude ~= R, 1 degree longitude ~= R*cos(lat).
    Sufficient for sub-kilometer junction geometry; error <0.1m at 50N within 1km.
    """
    earth_r = 6371000.0
    lat0_rad = math.radians(p_ref[0])
    dx = math.radians(p[1] - p_ref[1]) * math.cos(lat0_rad) * earth_r
    dy = math.radians(p[0] - p_ref[0]) * earth_r
    return dx, dy


def _vector_to_angle(vec: Tuple[float, float]) -> Optional[float]:
    """Angle from +X (east), CCW, in degrees [0, 360). None if vector is zero."""
    if abs(vec[0]) < 1e-9 and abs(vec[1]) < 1e-9:
        return None
    return math.degrees(math.atan2(vec[1], vec[0])) % 360.0


def _angle_to_compass(angle: float) -> str:
    """Bucket an arctan2 angle to N/E/S/W. Mirrors GPI standardizer (standardizer.py:149)."""
    a = angle % 360.0
    if 225.0 <= a < 315.0:
        return "N"
    if 135.0 <= a < 225.0:
        return "E"
    if 45.0 <= a < 135.0:
        return "S"
    return "W"


def _circular_angle_delta(a: float, b: float) -> float:
    """Smallest unsigned angular distance between two bearings, in degrees."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _road_angle_into_cross(road: RealRoad, cross_id: int, center: Tuple[float, float]) -> Optional[float]:
    """Compute GPI-style angle for `road` as observed at `cross_id`.

    The vector points INTO the junction stop line, same convention as
    Service-ai standardizer (`_compute_lane_vector`). Returns the arctan2 angle
    in [0, 360) so callers can both bucket compass direction AND pick the
    candidate closest to the ideal angle on collisions.

    Returns None if the polyline is missing/degenerate so caller can fall back.
    """
    polyline = road.coordinates
    if len(polyline) < 2:
        return None

    # Identify which endpoint is the junction stop line: the one closer to cross center.
    p_first, p_last = polyline[0], polyline[-1]
    d_first = math.hypot(*_enu_offset(center, p_first))
    d_last = math.hypot(*_enu_offset(center, p_last))
    if d_first <= d_last:
        # First point near junction; vector goes from polyline[1] (approach) -> polyline[0] (stop).
        p_prev, p_stop = polyline[1], p_first
    else:
        p_prev, p_stop = polyline[-2], p_last

    # All offsets in the same local ENU frame anchored at cross center.
    prev = _enu_offset(center, p_prev)
    stop = _enu_offset(center, p_stop)
    vec = (stop[0] - prev[0], stop[1] - prev[1])
    return _vector_to_angle(vec)


def _select_best_per_direction(
    candidates_by_dir: Dict[str, List[Tuple[RealRoad, float]]],
) -> Dict[str, RealRoad]:
    """Per-direction, pick the candidate whose angle is closest to the ideal bearing.

    Mirrors Service-ai standardizer collision logic (standardizer.py:222-227):
        best_edge = min(candidates, key=lambda x: circular_delta(x.angle, ideal))
    """
    chosen: Dict[str, RealRoad] = {}
    for direction, cands in candidates_by_dir.items():
        if not cands:
            continue
        ideal = _IDEAL_ANGLES[direction]
        best_road, _ = min(cands, key=lambda ra: _circular_angle_delta(ra[1], ideal))
        chosen[direction] = best_road
    return chosen


def _default_int(value: Optional[int], fallback: int) -> int:
    return int(value) if value not in (None, 0) else int(fallback)


def _default_float(value: Optional[float], fallback: float) -> float:
    return float(value) if value not in (None, 0) else float(fallback)


def _fetch_all(engine, sql: str, params: dict) -> List[dict]:
    stmt = text(sql)
    if "ids" in params:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()
    return [dict(r) for r in rows]


def _get_value(row: dict, *keys: str, default=None):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


def _is_active(row: dict) -> bool:
    value = _get_value(row, "is_active", "isActive", "IS_ACTIVE", default=1)
    return value in (None, True, 1, "1", "true", "TRUE")


def _load_real_network_snapshot(engine, area_id: int) -> Optional[dict]:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT payload_json
                    FROM real_network_snapshot
                    WHERE area_id = :area_id
                    """
                ),
                {"area_id": area_id},
            ).mappings().first()
    except Exception:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except Exception as e:
        raise ValueError(f"Invalid real_network_snapshot payload area_id={area_id}: {e}") from e
    return payload


def fetch_area_crosses(engine, area_id: int) -> List[dict]:
    return _fetch_all(
        engine,
        """
        SELECT area_id, cross_id, cycle_id, is_active
        FROM v_area_cross
        WHERE area_id = :area_id AND (is_active = 1 OR is_active IS NULL)
        """,
        {"area_id": area_id},
    )


def fetch_crosses(engine, cross_ids: Iterable[int]) -> List[dict]:
    ids = list(cross_ids)
    if not ids:
        return []
    return _fetch_all(
        engine,
        """
        SELECT id, cross_name, location
        FROM v_cross
        WHERE id IN :ids AND (is_active = 1 OR is_active IS NULL)
        """,
        {"ids": tuple(ids)},
    )


def fetch_road_coordinates(engine, road_ids: Iterable[int]) -> Dict[int, List[Tuple[float, float]]]:
    """Pull v_road_coordinate polylines ordered by `order_number` for each road id."""
    ids = list(road_ids)
    if not ids:
        return {}
    rows = _fetch_all(
        engine,
        """
        SELECT road_id, order_number, latitude, longitude
        FROM v_road_coordinate
        WHERE road_id IN :ids
        ORDER BY road_id, order_number
        """,
        {"ids": tuple(ids)},
    )
    out: Dict[int, List[Tuple[float, float]]] = {}
    for r in rows:
        rid = int(r["road_id"])
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        out.setdefault(rid, []).append((lat, lon))
    return out


def fetch_roads(engine, cross_ids: Iterable[int]) -> List[RealRoad]:
    ids = list(cross_ids)
    if not ids:
        return []
    rows = _fetch_all(
        engine,
        """
        SELECT id, from_cross, to_cross, from_cross_direction, to_cross_direction,
               number_of_lanes, length, speed_design, capacity_design
        FROM v_road
        WHERE (from_cross IN :ids OR to_cross IN :ids)
          AND (is_active = 1 OR is_active IS NULL)
        """,
        {"ids": tuple(ids)},
    )
    out: List[RealRoad] = []
    for r in rows:
        out.append(
            RealRoad(
                id=int(r["id"]),
                from_cross=int(r["from_cross"]) if r.get("from_cross") else None,
                to_cross=int(r["to_cross"]) if r.get("to_cross") else None,
                from_dir=int(r["from_cross_direction"]) if r.get("from_cross_direction") else None,
                to_dir=int(r["to_cross_direction"]) if r.get("to_cross_direction") else None,
                lanes=r.get("number_of_lanes"),
                length_m=r.get("length"),
                speed_design=r.get("speed_design"),
                capacity_design=r.get("capacity_design"),
            )
        )
    coords_by_road = fetch_road_coordinates(engine, [road.id for road in out])
    for road in out:
        road.coordinates = coords_by_road.get(road.id, [])
    return out


def fetch_cross_centers(engine, cross_ids: Iterable[int]) -> Dict[int, Tuple[float, float]]:
    """Pull v_cross.location and parse as (lat, lon). Missing/invalid -> not in dict."""
    rows = fetch_crosses(engine, cross_ids)
    out: Dict[int, Tuple[float, float]] = {}
    for r in rows:
        center = _parse_location_string(r.get("location"))
        if center is not None:
            out[int(r["id"])] = center
    return out


def _parse_location_string(value) -> Optional[Tuple[float, float]]:
    """Parse v_cross.location which is "lat,lon"."""
    if not isinstance(value, str) or "," not in value:
        return None
    try:
        lat_s, lon_s = value.split(",", 1)
        return float(lat_s.strip()), float(lon_s.strip())
    except (TypeError, ValueError):
        return None


def fetch_cycles(engine, cross_ids: Iterable[int]) -> List[RealCycle]:
    ids = list(cross_ids)
    if not ids:
        return []
    rows = _fetch_all(
        engine,
        """
        SELECT id, cross_id, cycle_type, cycle_length, cycle_name, created_date,
               yellow, red_clear
        FROM v_cycle
        WHERE cross_id IN :ids AND (is_active = 1 OR is_active IS NULL)
        """,
        {"ids": tuple(ids)},
    )
    return [
        RealCycle(
            id=int(r["id"]),
            cross_id=int(r["cross_id"]),
            cycle_type=int(r["cycle_type"]),
            cycle_length=_default_int(r.get("cycle_length"), 0) or None,
            cycle_name=r.get("cycle_name"),
            created_date=str(r["created_date"]) if r.get("created_date") is not None else None,
            yellow=_default_int(r.get("yellow"), 0) or None,
            red_clear=_default_int(r.get("red_clear"), 0) if r.get("red_clear") is not None else None,
        )
        for r in rows
    ]


def fetch_stages(engine, cycle_ids: Iterable[int]) -> List[RealStage]:
    ids = list(cycle_ids)
    if not ids:
        return []
    rows = _fetch_all(
        engine,
        """
        SELECT id, cycle_id, order_number, stage_code, old_id, green, yellow,
               red_clear, min_green_time, max_green_time
        FROM v_stage
        WHERE cycle_id IN :ids AND (is_active = 1 OR is_active IS NULL)
        ORDER BY cycle_id, order_number
        """,
        {"ids": tuple(ids)},
    )
    return [
        RealStage(
            id=int(r["id"]),
            cycle_id=int(r["cycle_id"]),
            order_number=int(r["order_number"]),
            stage_code=r.get("stage_code"),
            old_id=r.get("old_id"),
            green=_default_int(r.get("green"), 0) if r.get("green") is not None else None,
            yellow=_default_int(r.get("yellow"), 0) if r.get("yellow") is not None else None,
            red_clear=_default_int(r.get("red_clear"), 0) if r.get("red_clear") is not None else None,
            min_green_time=_default_int(r.get("min_green_time"), 0) if r.get("min_green_time") is not None else None,
            max_green_time=_default_int(r.get("max_green_time"), 0) if r.get("max_green_time") is not None else None,
        )
        for r in rows
    ]


def _snapshot_area_crosses(snapshot: dict, area_id: int) -> List[dict]:
    rows = snapshot.get("area_crosses") or snapshot.get("areaCrosses") or []
    out: List[dict] = []
    for r in rows:
        if not _is_active(r):
            continue
        rid = _get_value(r, "area_id", "areaId", "AREA_ID", default=area_id)
        if int(rid) != int(area_id):
            continue
        out.append(
            {
                "area_id": area_id,
                "cross_id": int(_get_value(r, "cross_id", "crossId", "CROSS_ID")),
                "cycle_id": _get_value(r, "cycle_id", "cycleId", "CYCLE_ID"),
                "is_active": _get_value(r, "is_active", "isActive", "IS_ACTIVE", default=1),
                "main_phase": _get_value(r, "main_phase", "mainPhase", "MAIN_PHASE"),
            }
        )
    return out


def _snapshot_roads(snapshot: dict, cross_ids: Iterable[int]) -> List[RealRoad]:
    in_set = set(int(x) for x in cross_ids)
    # v_road_coordinate may be sent either as a top-level table
    # (`road_coordinates` / `roadCoordinates`) or inlined under each road
    # (`coordinates`). Build a lookup for the top-level form once.
    top_level_coords: Dict[int, List[Tuple[float, float]]] = {}
    for entry in snapshot.get("road_coordinates") or snapshot.get("roadCoordinates") or []:
        rid_raw = _get_value(entry, "road_id", "roadId", "ROAD_ID")
        if rid_raw in (None, ""):
            continue
        try:
            rid = int(rid_raw)
            order = int(_get_value(entry, "order_number", "orderNumber", "ORDER_NUMBER", default=0))
            lat = float(_get_value(entry, "latitude", "lat", "LATITUDE"))
            lon = float(_get_value(entry, "longitude", "lon", "lng", "LONGITUDE"))
        except (TypeError, ValueError):
            continue
        top_level_coords.setdefault(rid, []).append((order, lat, lon))
    for rid, pts in top_level_coords.items():
        pts.sort(key=lambda t: t[0])

    out: List[RealRoad] = []
    for r in snapshot.get("roads", []):
        if not _is_active(r):
            continue
        from_cross = _get_value(r, "from_cross", "fromCross", "FROM_CROSS")
        to_cross = _get_value(r, "to_cross", "toCross", "TO_CROSS")
        from_cross = int(from_cross) if from_cross not in (None, "") else None
        to_cross = int(to_cross) if to_cross not in (None, "") else None
        if from_cross not in in_set and to_cross not in in_set:
            continue
        road = RealRoad(
            id=int(_get_value(r, "id", "road_id", "roadId", "ID")),
            from_cross=from_cross,
            to_cross=to_cross,
            from_dir=_get_value(r, "from_cross_direction", "fromCrossDirection", "FROM_CROSS_DIRECTION"),
            to_dir=_get_value(r, "to_cross_direction", "toCrossDirection", "TO_CROSS_DIRECTION"),
            lanes=_get_value(r, "number_of_lanes", "numberOfLanes", "lanes", "NUMBER_OF_LANES"),
            length_m=_get_value(r, "length", "length_m", "lengthMeters", "LENGTH"),
            speed_design=_get_value(r, "speed_design", "speedDesign", "speed_design_kmh", "SPEED_DESIGN"),
            capacity_design=_get_value(r, "capacity_design", "capacityDesign", "saturation_flow", "CAPACITY_DESIGN"),
        )
        road.coordinates = _extract_road_polyline(r, top_level_coords.get(road.id, []))
        out.append(road)
    return out


def _extract_road_polyline(
    row: dict,
    top_level_pts: List[Tuple[int, float, float]],
) -> List[Tuple[float, float]]:
    """Resolve polyline from inline `coordinates` or top-level `road_coordinates` entries."""
    inline = row.get("coordinates")
    pts: List[Tuple[int, float, float]] = []
    if isinstance(inline, list):
        for c in inline:
            if not isinstance(c, dict):
                continue
            try:
                order = int(_get_value(c, "order_number", "orderNumber", "ORDER_NUMBER", default=0))
                lat = float(_get_value(c, "latitude", "lat", "LATITUDE"))
                lon = float(_get_value(c, "longitude", "lon", "lng", "LONGITUDE"))
            except (TypeError, ValueError):
                continue
            pts.append((order, lat, lon))
    if not pts and top_level_pts:
        pts = list(top_level_pts)
    pts.sort(key=lambda t: t[0])
    return [(lat, lon) for _, lat, lon in pts]


def _snapshot_cross_centers(snapshot: dict, cross_ids: Iterable[int]) -> Dict[int, Tuple[float, float]]:
    """Read cross centers from snapshot crosses (`center_coordinate` or `location`)."""
    in_set = set(int(x) for x in cross_ids)
    centers: Dict[int, Tuple[float, float]] = {}
    for r in snapshot.get("crosses", []):
        cid_raw = _get_value(r, "id", "cross_id", "crossId", "ID")
        if cid_raw in (None, ""):
            continue
        try:
            cid = int(cid_raw)
        except (TypeError, ValueError):
            continue
        if cid not in in_set:
            continue
        cc = r.get("center_coordinate") or r.get("centerCoordinate")
        if isinstance(cc, dict):
            lat = _get_value(cc, "latitude", "lat", "LATITUDE")
            lon = _get_value(cc, "longitude", "lon", "lng", "LONGITUDE")
            try:
                centers[cid] = (float(lat), float(lon))
                continue
            except (TypeError, ValueError):
                pass
        center = _parse_location_string(_get_value(r, "location", "LOCATION"))
        if center is not None:
            centers[cid] = center
    return centers


def _snapshot_cycles(snapshot: dict, cross_ids: Iterable[int]) -> List[RealCycle]:
    in_set = set(int(x) for x in cross_ids)
    out: List[RealCycle] = []
    for r in snapshot.get("cycles", []):
        if not _is_active(r):
            continue
        cross_id = _get_value(r, "cross_id", "crossId", "CROSS_ID")
        if cross_id in (None, "") or int(cross_id) not in in_set:
            continue
        out.append(
            RealCycle(
                id=int(_get_value(r, "id", "cycle_id", "cycleId", "ID")),
                cross_id=int(cross_id),
                cycle_type=int(_get_value(r, "cycle_type", "cycleType", "CYCLE_TYPE", default=0)),
                cycle_length=(
                    int(_get_value(r, "cycle_length", "cycleLength", "CYCLE_LENGTH"))
                    if _get_value(r, "cycle_length", "cycleLength", "CYCLE_LENGTH") not in (None, "")
                    else None
                ),
                cycle_name=_get_value(r, "cycle_name", "cycleName", "crossName", "CYCLE_NAME"),
                created_date=_get_value(r, "created_date", "createdDate", "CREATED_DATE"),
                yellow=(
                    int(_get_value(r, "yellow", "YELLOW"))
                    if _get_value(r, "yellow", "YELLOW") not in (None, "")
                    else None
                ),
                red_clear=(
                    int(_get_value(r, "red_clear", "redClear", "RED_CLEAR"))
                    if _get_value(r, "red_clear", "redClear", "RED_CLEAR") not in (None, "")
                    else None
                ),
            )
        )
    return out


def _snapshot_stages(snapshot: dict, cycle_ids: Iterable[int]) -> List[RealStage]:
    in_set = set(int(x) for x in cycle_ids)
    out: List[RealStage] = []
    for r in snapshot.get("stages", []):
        if not _is_active(r):
            continue
        cycle_id = _get_value(r, "cycle_id", "cycleId", "CYCLE_ID")
        if cycle_id in (None, "") or int(cycle_id) not in in_set:
            continue
        out.append(
            RealStage(
                id=int(_get_value(r, "id", "stage_id", "stageId", "ID")),
                cycle_id=int(cycle_id),
                order_number=int(_get_value(r, "order_number", "orderNumber", "ORDER_NUMBER", default=1)),
                stage_code=_get_value(r, "stage_code", "stageCode", "STAGE_CODE"),
                old_id=_get_value(r, "old_id", "oldId", "OLD_ID"),
                green=(
                    int(_get_value(r, "green", "greenTime", "GREEN"))
                    if _get_value(r, "green", "greenTime", "GREEN") not in (None, "")
                    else None
                ),
                yellow=(
                    int(_get_value(r, "yellow", "YELLOW"))
                    if _get_value(r, "yellow", "YELLOW") not in (None, "")
                    else None
                ),
                red_clear=(
                    int(_get_value(r, "red_clear", "redClear", "RED_CLEAR"))
                    if _get_value(r, "red_clear", "redClear", "RED_CLEAR") not in (None, "")
                    else None
                ),
                min_green_time=(
                    int(_get_value(r, "min_green_time", "minGreenTime", "MIN_GREEN_TIME"))
                    if _get_value(r, "min_green_time", "minGreenTime", "MIN_GREEN_TIME") not in (None, "")
                    else None
                ),
                max_green_time=(
                    int(_get_value(r, "max_green_time", "maxGreenTime", "MAX_GREEN_TIME"))
                    if _get_value(r, "max_green_time", "maxGreenTime", "MAX_GREEN_TIME") not in (None, "")
                    else None
                ),
            )
        )
    return sorted(out, key=lambda s: (s.cycle_id, s.order_number))


def _classify_road_at_cross(
    road: RealRoad,
    cross_id: int,
    centers: Dict[int, Tuple[float, float]],
    legacy_encoding: Dict[int, str],
) -> Optional[Tuple[str, float]]:
    """Determine the (compass_direction, arctan2_angle) of `road` at `cross_id`.

    Strategy mirrors Service-ai GPI:
      1. If the cross center + polyline are available, compute the vector INTO the
         junction stop line and bucket via the same compass cutoffs.
      2. Otherwise, fall back to the legacy from/to_cross_direction encoding. The
         returned angle is the *ideal* bearing for that compass quadrant, so the
         "closest-to-ideal" tiebreaker treats every fallback candidate as a
         perfect match in its assigned bucket (no preference vs. another
         fallback candidate).
    """
    center = centers.get(cross_id)
    if center is not None:
        angle = _road_angle_into_cross(road, cross_id, center)
        if angle is not None:
            return _angle_to_compass(angle), angle

    legacy_code = road.from_dir if road.from_cross == cross_id else (
        road.to_dir if road.to_cross == cross_id else None
    )
    compass = _legacy_direction_compass(legacy_code, legacy_encoding)
    if compass is not None:
        return compass, _IDEAL_ANGLES[compass]
    return None


def _candidates_per_direction(
    roads: List[RealRoad],
    cross_id: int,
    centers: Dict[int, Tuple[float, float]],
    legacy_encoding: Dict[int, str],
) -> Dict[str, List[Tuple[RealRoad, float]]]:
    """Group all roads touching `cross_id` by their inferred compass direction."""
    out: Dict[str, List[Tuple[RealRoad, float]]] = {d: [] for d in _NESW}
    for road in roads:
        if road.from_cross != cross_id and road.to_cross != cross_id:
            continue
        classified = _classify_road_at_cross(road, cross_id, centers, legacy_encoding)
        if classified is None:
            continue
        direction, angle = classified
        out[direction].append((road, angle))
    return out


def _build_neighbors(
    roads: List[RealRoad],
    cross_ids: List[int],
    centers: Dict[int, Tuple[float, float]],
    legacy_encoding: Dict[int, str],
) -> Dict[int, List[dict]]:
    neighbors: Dict[int, List[dict]] = {cid: [] for cid in cross_ids}
    in_set = set(cross_ids)
    seen: Dict[int, set] = {cid: set() for cid in cross_ids}

    for road in roads:
        if road.from_cross is None or road.to_cross is None:
            continue
        if road.from_cross not in in_set or road.to_cross not in in_set:
            continue
        if road.from_cross == road.to_cross:
            continue

        from_class = _classify_road_at_cross(road, road.from_cross, centers, legacy_encoding)
        if from_class is not None and road.to_cross not in seen[road.from_cross]:
            neighbors[road.from_cross].append({
                "neighbor_id": road.to_cross,
                "direction": _DIR_INDEX[from_class[0]],
            })
            seen[road.from_cross].add(road.to_cross)

        to_class = _classify_road_at_cross(road, road.to_cross, centers, legacy_encoding)
        if to_class is not None and road.from_cross not in seen[road.to_cross]:
            neighbors[road.to_cross].append({
                "neighbor_id": road.from_cross,
                "direction": _DIR_INDEX[to_class[0]],
            })
            seen[road.to_cross].add(road.from_cross)

    for cid in neighbors:
        neighbors[cid] = neighbors[cid][:MAX_NEIGHBORS]
    return neighbors


def _build_direction_map(
    candidates_by_dir: Dict[str, List[Tuple[RealRoad, float]]],
) -> Dict[str, int]:
    """Pick exactly one road per direction using the GPI tiebreaker.

    Output keys are stringified road ids and values are direction indices in
    {0:N, 1:E, 2:S, 3:W}. A direction is absent if no road maps there. Matches
    the structure consumed by `composer._real_road_by_direction`.
    """
    chosen = _select_best_per_direction(candidates_by_dir)
    return {str(road.id): _DIR_INDEX[direction] for direction, road in chosen.items()}


def _observation_mask(direction_map: Dict[str, int]) -> List[int]:
    has_dir = [0, 0, 0, 0]
    for d in direction_map.values():
        if 0 <= d < 4:
            has_dir[d] = 1
    mask: List[int] = []
    for d in range(4):
        mask.extend([has_dir[d]] * 3)
    return mask


def _roads_static(roads: List[RealRoad], cross_id: int) -> Dict[str, dict]:
    """Static road properties indexed by road id for every road touching the cross.

    The composer only looks up the roads selected in `direction_map`, but we keep
    every related road here so downstream consumers (debugging, future heuristics)
    have access to lanes/length/speed metadata.
    """
    out: Dict[str, dict] = {}
    for road in roads:
        if road.from_cross != cross_id and road.to_cross != cross_id:
            continue
        out[str(road.id)] = {
            "lanes": _default_int(road.lanes, DEFAULT_ROAD_LANES),
            "length_meters": _default_float(road.length_m, DEFAULT_ROAD_LENGTH_M),
            "speed_design_kmh": _default_float(road.speed_design, DEFAULT_ROAD_SPEED_DESIGN_KMH),
            "saturation_flow": _default_float(road.capacity_design, DEFAULT_ROAD_SATURATION_FLOW),
        }
    return out


def _build_cycles(
    cycles: List[RealCycle],
    stages: List[RealStage],
    primary_cycle_id: Optional[int],
) -> Dict[str, dict]:
    by_cycle: Dict[int, List[RealStage]] = {}
    for st in stages:
        by_cycle.setdefault(st.cycle_id, []).append(st)

    out: Dict[str, dict] = {}
    for cy in cycles:
        items = sorted(by_cycle.get(cy.id, []), key=lambda s: s.order_number)
        stage_to_std: Dict[str, int] = {}
        std_to_stage: Dict[str, int] = {}
        for idx, st in enumerate(items):
            std_idx = idx if idx < 8 else -1
            stage_to_std[str(st.id)] = std_idx
            if std_idx >= 0 and str(std_idx) not in std_to_stage:
                std_to_stage[str(std_idx)] = st.id
        out[str(cy.id)] = {
            "stage_to_standard_phase": stage_to_std,
            "standard_phase_to_stage": std_to_stage,
            "is_primary": primary_cycle_id == cy.id,
            "num_stages": len(items),
            "cycle_type": cy.cycle_type,
            "cycle_length": cy.cycle_length,
            "cycle_name": cy.cycle_name,
            "created_date": cy.created_date,
            "yellow": cy.yellow,
            "red_clear": cy.red_clear,
            "stages": [
                {
                    "id": st.id,
                    "order_number": st.order_number,
                    "stage_code": st.stage_code,
                    "old_id": st.old_id,
                    "green": st.green,
                    "yellow": st.yellow,
                    "red_clear": st.red_clear,
                    "min_green_time": st.min_green_time,
                    "max_green_time": st.max_green_time,
                }
                for st in items
            ],
        }
    return out


def _choose_primary_cycle(area_cross_row: Optional[dict], cycles: List[RealCycle]) -> Optional[int]:
    if area_cross_row and area_cross_row.get("cycle_id"):
        return int(area_cross_row["cycle_id"])
    if not cycles:
        return None
    # Prefer cycle_type=0 (goc). Else first available.
    for cy in cycles:
        if cy.cycle_type == 0:
            return cy.id
    return cycles[0].id


def compile_real_normalization(
    *,
    db_url: str,
    area_id: int,
    output_dir: Path,
) -> dict:
    engine = create_engine(db_url, future=True)
    snapshot = _load_real_network_snapshot(engine, area_id)
    source = "service_snapshot" if snapshot is not None else "management_views"

    if snapshot is not None:
        area_cross = _snapshot_area_crosses(snapshot, area_id)
    else:
        area_cross = fetch_area_crosses(engine, area_id)

    cross_ids = sorted({int(r["cross_id"]) for r in area_cross})
    if not cross_ids:
        raise ValueError(f"No active crosses found for area_id={area_id}")

    if snapshot is not None:
        roads = _snapshot_roads(snapshot, cross_ids)
        cycles = _snapshot_cycles(snapshot, cross_ids)
        stages = _snapshot_stages(snapshot, [c.id for c in cycles])
        centers = _snapshot_cross_centers(snapshot, cross_ids)
    else:
        roads = fetch_roads(engine, cross_ids)
        cycles = fetch_cycles(engine, cross_ids)
        stages = fetch_stages(engine, [c.id for c in cycles])
        centers = fetch_cross_centers(engine, cross_ids)

    area_cross_by_cross = {int(r["cross_id"]): r for r in area_cross}
    cycles_by_cross: Dict[int, List[RealCycle]] = {}
    for cy in cycles:
        cycles_by_cross.setdefault(cy.cross_id, []).append(cy)

    crosses_missing_center = [cid for cid in cross_ids if cid not in centers]
    if crosses_missing_center:
        logger.warning(
            "[real_normalization] %d cross(es) missing GPS center, will fall back to "
            "legacy from_cross_direction encoding: %s",
            len(crosses_missing_center),
            crosses_missing_center,
        )

    legacy_encoding = _detect_legacy_direction_encoding(roads)
    logger.info(
        "[real_normalization] legacy direction encoding detected: %s",
        "4-dir (1..4)" if legacy_encoding is _LEGACY_DIRECTION_CODE_4DIR else "8-dir (0/2/4/6)",
    )

    normalized_crosses: List[dict] = []
    for cid in cross_ids:
        cross_cycles = cycles_by_cross.get(cid, [])
        primary_cycle_id = _choose_primary_cycle(area_cross_by_cross.get(cid), cross_cycles)

        candidates = _candidates_per_direction(roads, cid, centers, legacy_encoding)
        direction_map = _build_direction_map(candidates)
        if not direction_map:
            logger.error(
                "[real_normalization] cross %s khong xac dinh duoc huong nao tu coordinate/legacy. "
                "Composer se raise loi khi compose sim bundle cho nut nay.",
                cid,
            )

        cross_obj = {
            "real_cross_id": cid,
            "primary_cycle_id": primary_cycle_id,
            "direction_map": direction_map,
            "observation_mask": _observation_mask(direction_map),
            "roads_static": _roads_static(roads, cid),
            "cycles": _build_cycles(cross_cycles, stages, primary_cycle_id),
        }
        normalized_crosses.append(cross_obj)

    neighbors = _build_neighbors(roads, cross_ids, centers, legacy_encoding)
    network = {
        "area_id": area_id,
        "cross_ids": cross_ids,
        "neighbors": {str(k): v for k, v in neighbors.items()},
        "max_neighbors": MAX_NEIGHBORS,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "intersections").mkdir(parents=True, exist_ok=True)

    for c in normalized_crosses:
        cross_id = int(c["real_cross_id"])
        cfg = {
            "cross_id": cross_id,
            "direction_map": c["direction_map"],
            "observation_mask": c["observation_mask"],
            "primary_cycle_id": c["primary_cycle_id"],
            "cycles": c["cycles"],
            "roads_static": c["roads_static"],
        }
        path = output_dir / "intersections" / f"cross_{cross_id}.json"
        path.write_text(
            _json_dump(cfg),
            encoding="utf-8",
        )

    (output_dir / "network.json").write_text(_json_dump(network), encoding="utf-8")

    payload = {
        "area_id": area_id,
        "network_id": snapshot.get("network_id") if snapshot else None,
        "tenant_id": snapshot.get("tenant_id") if snapshot else None,
        "source": source,
        "generated_at": _now_iso(),
        "sim_to_real": snapshot.get("sim_to_real", {}) if snapshot else {},
        "crosses": normalized_crosses,
    }
    (output_dir / "real_normalization.json").write_text(_json_dump(payload), encoding="utf-8")

    logger.info(
        f"[real_normalization] built area={area_id} source={source} "
        f"crosses={len(cross_ids)} -> {output_dir}"
    )
    return payload


def _json_dump(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)

