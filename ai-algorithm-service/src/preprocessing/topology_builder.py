"""
Auto-build topology normalization config for a whole area from a single request.

Chạy một lần duy nhất cho mỗi area — lần đầu tiên service thấy areaId mới (hoặc
file cấu hình bị xóa). Từ dữ liệu có sẵn trong request:
  - Tọa độ cross (Cross.x, Cross.y)
  - Hướng các road (Road.direction = 1..4 N/E/S/W)
  - Liên kết cross láng giềng qua Road.toCrossId (nếu có) hoặc tọa độ
  - Thứ tự stage + lamp/movement pattern (heuristic phase mapping)

Ghi ra:
  <area_dir>/network.json                       — danh sách cross, ma trận neighbor
  <area_dir>/intersections/cross_<id>.json      — config mỗi cross

Kết quả được CACHE trên disk + in-memory. Không regenerate trừ khi bị xóa thủ công.
"""

from __future__ import annotations

import math
from typing import Dict, List

from src.core.config import get_settings
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.preprocessing.intersection_registry import (
    IntersectionConfig,
    area_dir,
    get_config,
    load_network,
    save_config,
    save_network,
)
from src.preprocessing.topology_normalizer import NUM_DIRECTIONS
from src.schemas.common_schemas.cross import Cross

MAX_NEIGHBORS = 4

# Cold-start fallback only. Authoritative GPS-based direction inference lives in
# `src/ops/real_normalization.py`. Keeping the 1..4 mapping here matches
# topology_normalizer for backward compatibility.
_DB_DIRECTION_MAP = {1: 0, 2: 1, 3: 2, 4: 3}


def _infer_direction_map(cross: Cross) -> Dict[str, int]:
    """Tạo direction_map từ Road.direction (DB field 1..4)."""
    out: Dict[str, int] = {}
    for road in cross.roads:
        if road.direction is not None:
            d = _DB_DIRECTION_MAP.get(int(road.direction))
            if d is not None:
                out[str(road.id)] = d
    # Fallback: round-robin cho road không có direction
    if not out and cross.roads:
        for i, road in enumerate(cross.roads):
            out[str(road.id)] = i % NUM_DIRECTIONS
    return out


def _infer_phase_mapping(cross: Cross) -> List[int]:
    """
    Heuristic identity mapping stage_index -> std_phase.

    DEPRECATED: heuristic này KHÔNG match training (sim NEMA mapping) — sẽ
    khiến policy chọn pha sai. Chỉ giữ cho dev/test bundle hoàn toàn không có
    commissioning. Production bắt buộc bundle v2 cung cấp cycle/stage_id mapping.
    """
    n = len(cross.stages)
    return [i if i < 8 else -1 for i in range(n)]


def _infer_observation_mask(cross: Cross, direction_map: Dict[str, int]) -> List[int]:
    """1 nếu direction đó có ít nhất một road, else 0. Mỗi direction có 3 lane."""
    dir_has = [0, 0, 0, 0]
    for d in direction_map.values():
        if 0 <= d < NUM_DIRECTIONS:
            dir_has[d] = 1
    mask: List[int] = []
    for d in range(NUM_DIRECTIONS):
        mask.extend([dir_has[d]] * 3)
    return mask


def _direction_from_bearing(dx: float, dy: float) -> int:
    """(dx,dy) → direction 0=N,1=E,2=S,3=W (y tăng = Bắc)."""
    angle = math.degrees(math.atan2(dx, dy))  # 0=N, 90=E
    if angle < 0:
        angle += 360
    sector = int((angle + 45) // 90) % 4  # 0..3 = N,E,S,W
    return sector


def _build_neighbors(
    crosses: List[Cross],
    distance_threshold: float = 2000.0,
) -> Dict[int, List[Dict]]:
    """
    Build neighbor map: cross_id -> list of {"neighbor_id": int, "direction": 0..3}.

    Ưu tiên dùng Road.toCrossId + Road.direction. Fallback sang tọa độ nếu cross
    có x,y.
    """
    ids = [c.id for c in crosses]
    by_id: Dict[int, Cross] = {c.id: c for c in crosses}
    neighbors: Dict[int, List[Dict]] = {c.id: [] for c in crosses}

    # Pass 1: explicit link qua Road.toCrossId
    for c in crosses:
        for road in c.roads:
            if road.toCrossId and road.toCrossId in by_id and road.toCrossId != c.id:
                d = _DB_DIRECTION_MAP.get(int(road.direction)) if road.direction else None
                if d is None:
                    continue
                if not any(n["neighbor_id"] == road.toCrossId for n in neighbors[c.id]):
                    neighbors[c.id].append({"neighbor_id": road.toCrossId, "direction": d})

    # Pass 2: fallback bằng tọa độ cho cross chưa có đủ neighbor
    for c in crosses:
        if neighbors[c.id] or c.x is None or c.y is None:
            continue
        candidates = []
        for o in crosses:
            if o.id == c.id or o.x is None or o.y is None:
                continue
            dx, dy = o.x - c.x, o.y - c.y
            dist = math.hypot(dx, dy)
            if dist > distance_threshold or dist == 0:
                continue
            candidates.append((dist, o.id, _direction_from_bearing(dx, dy)))
        candidates.sort()
        # Pick closest 1 per direction, up to MAX_NEIGHBORS
        seen_dirs = set()
        for dist, nid, d in candidates:
            if d in seen_dirs:
                continue
            seen_dirs.add(d)
            neighbors[c.id].append({"neighbor_id": nid, "direction": d})
            if len(neighbors[c.id]) >= MAX_NEIGHBORS:
                break

    # Truncate
    for cid in neighbors:
        neighbors[cid] = neighbors[cid][:MAX_NEIGHBORS]

    return neighbors


def ensure_area_configs(area_id: int, crosses: List[Cross]) -> dict:
    """
    Đảm bảo area_<id>/network.json + per-cross configs tồn tại. Nếu thiếu — auto-
    generate từ `crosses` trong request hiện tại và lưu disk.

    Returns:
        network dict: {"area_id", "cross_ids", "neighbors", "max_neighbors"}
    """
    net = load_network(area_id)
    existing_cross_ids = set(net["cross_ids"]) if net else set()
    request_cross_ids = {c.id for c in crosses}
    missing_cross_configs = [c for c in crosses if get_config(area_id, c.id) is None]

    # Đủ điều kiện reuse: network tồn tại + chứa tất cả cross trong request + các file config có đủ
    if net and request_cross_ids.issubset(existing_cross_ids) and not missing_cross_configs:
        return net

    # Plan 6.1.1: production strict mode KHONG auto-generate. Thieu config -> fail-fast.
    if get_settings().ai_strict_mode:
        missing_ids = [c.id for c in missing_cross_configs]
        raise AlgorithmException(
            (
                f"Area {area_id} thieu config tren disk cho cross={missing_ids}. "
                f"Strict mode bat -> khong auto-generate."
            ),
            code=ErrorCode.CONFIG_NOT_FOUND,
            area_id=area_id,
            extra={"missingCrossIds": missing_ids},
        )

    # Rebuild (một phần hoặc toàn bộ) — chi chay o non-strict (dev/staging).
    logger.info(f"Auto-generating topology config cho area={area_id} ({len(crosses)} cross)")

    for c in crosses:
        if get_config(area_id, c.id) is not None:
            continue
        direction_map = _infer_direction_map(c)
        cfg = IntersectionConfig(
            cross_id=c.id,
            direction_map=direction_map,
            phase_mapping=_infer_phase_mapping(c),
            observation_mask=_infer_observation_mask(c, direction_map),
        )
        save_config(area_id, cfg)

    # Merge neighbors: giữ từ net cũ, bổ sung neighbor cho cross mới
    neighbors = _build_neighbors(crosses)
    if net and "neighbors" in net:
        merged = {int(k): v for k, v in net["neighbors"].items()}
        merged.update(neighbors)
        neighbors = merged

    all_cross_ids = sorted(set(existing_cross_ids) | request_cross_ids)
    network = {
        "area_id": area_id,
        "cross_ids": all_cross_ids,
        "neighbors": {str(k): v for k, v in neighbors.items()},
        "max_neighbors": MAX_NEIGHBORS,
    }
    save_network(area_id, network)
    return network


def get_neighbor_ids(network: dict, cross_id: int) -> List[Dict]:
    """Trả về list [{neighbor_id, direction}, ...] cho cross_id."""
    nbrs = network.get("neighbors", {})
    return nbrs.get(str(cross_id)) or nbrs.get(cross_id) or []


def area_policy_paths(area_id: int) -> Dict[str, str]:
    d = area_dir(area_id)
    return {
        "onnx": str(d / "policy.onnx"),
        "meta": str(d / "policy_meta.json"),
        "network": str(d / "network.json"),
        "intersections_dir": str(d / "intersections"),
    }
