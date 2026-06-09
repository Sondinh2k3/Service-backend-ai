"""Adapters for real-network sync payloads.

The service stores and compiles a flat snapshot shape that mirrors management
tables. API callers may send a compact nested shape; this module converts that
nested shape into the flat representation without changing the runtime
pipeline.
"""

from __future__ import annotations

from typing import Any, Dict, List


def first_present(data: Dict[str, Any], *keys: str, default=None):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def normalize_area(area: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(area or {})
    if "area_id" not in out and "id" in out:
        out["area_id"] = out["id"]
    if "area_name" not in out and "name" in out:
        out["area_name"] = out["name"]
    return out


def normalize_coordinates(points: Any) -> Any:
    """Accept compact [[lat, lon], ...] coordinates and emit v1 dict format."""
    if not isinstance(points, list):
        return points

    out: List[Any] = []
    for idx, point in enumerate(points, start=1):
        if isinstance(point, dict):
            item = dict(point)
            item.setdefault("order_number", idx)
            out.append(item)
            continue
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append(
                {
                    "order_number": idx,
                    "latitude": point[0],
                    "longitude": point[1],
                }
            )
            continue
        out.append(point)
    return out


def flatten_nested_real_network(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert compact nested real-network payload into the current flat v1 shape.

    Existing flat payloads are left untouched. This keeps runtime/compiler code
    stable while allowing callers to send `crosses[].roads/cycles/stages`.
    """
    crosses_raw = data.get("crosses")
    if not isinstance(crosses_raw, list):
        return data

    has_flat_tables = any(
        data.get(key) for key in ("areaCrosses", "area_crosses", "roads", "cycles", "stages")
    )
    has_nested_tables = any(
        isinstance(c, dict) and (c.get("roads") or c.get("cycles") or c.get("stages"))
        for c in crosses_raw
    )
    if has_flat_tables or not has_nested_tables:
        return data

    out = dict(data)
    out["area"] = normalize_area(dict(out.get("area") or {}))

    area_crosses: List[Dict[str, Any]] = []
    crosses: List[Dict[str, Any]] = []
    roads: List[Dict[str, Any]] = []
    cycles: List[Dict[str, Any]] = []
    stages: List[Dict[str, Any]] = []
    sim_to_real = dict(out.get("simToReal") or out.get("sim_to_real") or {})

    area_id = first_present(out["area"], "area_id", "areaId", "id")

    for cross in crosses_raw:
        if not isinstance(cross, dict):
            crosses.append(cross)
            continue

        cross_id = first_present(cross, "id", "cross_id", "crossId")
        if cross_id is None:
            crosses.append(dict(cross))
            continue

        primary_cycle_id = first_present(
            cross,
            "primaryCycleId",
            "primary_cycle_id",
            "cycle_id",
            "cycleId",
        )
        area_cross: Dict[str, Any] = {"cross_id": cross_id}
        if area_id is not None:
            area_cross["area_id"] = area_id
        if primary_cycle_id is not None:
            area_cross["cycle_id"] = primary_cycle_id
        area_crosses.append(area_cross)

        sim_id = first_present(cross, "simId", "sim_id", "old_id", "oldId")
        if sim_id is not None and str(sim_id) not in sim_to_real:
            sim_to_real[str(sim_id)] = cross_id

        crosses.append(_flatten_cross(cross, cross_id))
        roads.extend(_flatten_roads(cross.get("roads") or [], cross_id))

        for cycle in cross.get("cycles") or []:
            if not isinstance(cycle, dict):
                continue
            cycle_id = first_present(cycle, "id", "cycle_id", "cycleId")
            cycles.append(_flatten_cycle(cycle, cross_id, cycle_id))
            stages.extend(_flatten_stages(cycle.get("stages") or [], cycle_id))

        # Also accept cross-level stages for single-cycle compact payloads.
        stages.extend(_flatten_stages(cross.get("stages") or [], primary_cycle_id))

    out["areaCrosses"] = area_crosses
    out["crosses"] = crosses
    out["roads"] = roads
    out["cycles"] = cycles
    out["stages"] = stages
    out["simToReal"] = sim_to_real
    return out


def _flatten_cross(cross: Dict[str, Any], cross_id: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": cross_id}
    for src, dst in (
        ("location", "location"),
        ("center_coordinate", "center_coordinate"),
        ("centerCoordinate", "center_coordinate"),
        ("old_id", "old_id"),
        ("oldId", "old_id"),
        ("cross_name", "cross_name"),
        ("crossName", "cross_name"),
    ):
        value = cross.get(src)
        if value is not None:
            out[dst] = value
    return out


def _flatten_roads(road_items: List[Any], cross_id: Any) -> List[Dict[str, Any]]:
    roads: List[Dict[str, Any]] = []
    for road in road_items:
        if not isinstance(road, dict):
            continue
        road_out: Dict[str, Any] = {
            "id": first_present(road, "id", "road_id", "roadId"),
            "from_cross": first_present(road, "from_cross", "fromCross", default=cross_id),
            "from_cross_direction": first_present(
                road,
                "from_cross_direction",
                "fromCrossDirection",
                "direction",
            ),
            "to_cross": first_present(road, "to_cross", "toCross", "toCrossId"),
            "to_cross_direction": first_present(road, "to_cross_direction", "toCrossDirection"),
            "number_of_lanes": first_present(
                road,
                "number_of_lanes",
                "numberOfLanes",
                "lanes",
            ),
            "length": first_present(road, "length", "length_m", "lengthMeters"),
            "capacity_design": first_present(
                road,
                "capacity_design",
                "capacityDesign",
                "capacity",
                "saturation_flow",
                "saturationFlow",
            ),
            "speed_design": first_present(
                road,
                "speed_design",
                "speedDesign",
                "speed_design_kmh",
                "speedDesignKmh",
            ),
        }
        coords = first_present(road, "coordinates", "road_coordinates", "roadCoordinates")
        if coords is not None:
            road_out["coordinates"] = normalize_coordinates(coords)
        roads.append({k: v for k, v in road_out.items() if v is not None})
    return roads


def _flatten_cycle(cycle: Dict[str, Any], cross_id: Any, cycle_id: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": cycle_id,
        "cross_id": first_present(cycle, "cross_id", "crossId", default=cross_id),
        "cycle_type": first_present(cycle, "cycle_type", "cycleType", "type", default=0),
        "cycle_length": first_present(cycle, "cycle_length", "cycleLength", "length"),
        "cycle_name": first_present(cycle, "cycle_name", "cycleName", "name"),
        "created_date": first_present(cycle, "created_date", "createdDate"),
        "yellow": first_present(cycle, "yellow"),
        "red_clear": first_present(cycle, "red_clear", "redClear"),
        "old_id": first_present(cycle, "old_id", "oldId"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _flatten_stages(stage_items: List[Any], cycle_id: Any) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    for stage in stage_items:
        if not isinstance(stage, dict):
            continue
        stage_out: Dict[str, Any] = {
            "id": first_present(stage, "id", "stage_id", "stageId"),
            "cycle_id": first_present(stage, "cycle_id", "cycleId", default=cycle_id),
            "order_number": first_present(stage, "order_number", "orderNumber", "order"),
            "stage_code": first_present(stage, "stage_code", "stageCode"),
            "old_id": first_present(stage, "old_id", "oldId"),
            "green": first_present(stage, "green", "greenTime"),
            "yellow": first_present(stage, "yellow"),
            "red_clear": first_present(stage, "red_clear", "redClear"),
            "min_green_time": first_present(
                stage,
                "min_green_time",
                "minGreenTime",
                "minGreen",
            ),
            "max_green_time": first_present(
                stage,
                "max_green_time",
                "maxGreenTime",
                "maxGreen",
            ),
        }
        stages.append({k: v for k, v in stage_out.items() if v is not None})
    return stages
