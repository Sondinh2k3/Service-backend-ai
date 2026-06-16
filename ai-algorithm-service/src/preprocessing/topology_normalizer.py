"""
Topology normalization: raw Road list -> ma trận 12 lane x 4 feature chuẩn hóa.

Thứ tự ưu tiên xác định hướng cho mỗi Road:
1. config.direction_map[str(road.id)] (từ GPI offline) — chính xác nhất.
2. road.direction (DB field 1=N, 2=E, 3=S, 4=W) — thường có sẵn.
3. Round-robin theo thứ tự roads — fallback cuối.

Layout output: 12 lane = 4 hướng (N,E,S,W) * 3 lane, mỗi lane 4 feature
(density, queue, occupancy, speed). Output là lane-major shape `(12, C)`.
Thiếu lane/hướng thì padding theo training convention:
`density=0, queue=0, occupancy=0, speed=1`.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from src.core.config import get_settings
from src.core.logger import logger
from src.preprocessing.intersection_registry import IntersectionConfig
from src.schemas.common_schemas.cross import Cross
from src.schemas.common_schemas.road import Road


NUM_DIRECTIONS = 4
LANES_PER_DIRECTION = 3
TOTAL_LANES = NUM_DIRECTIONS * LANES_PER_DIRECTION  # 12
DEFAULT_CHANNEL_VALUES = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)

# DB direction field (1..4) -> standard index (0..3): 1=N,2=E,3=S,4=W.
# Note: production v_road may also use 8-direction encoding (0=N, 2=E, 4=S,
# 6=W). The authoritative direction inference lives in
# `src/ops/real_normalization.py` which uses cross/road GPS coordinates to
# avoid this ambiguity. This module is only the cold-start fallback when no
# IntersectionConfig has been generated, and we keep the historical 1..4
# mapping here to preserve behavior for existing deployments.
_DB_DIRECTION_MAP = {1: 0, 2: 1, 3: 2, 4: 3}


class _RoadMetricHistory:
    """Rolling mean for normalized road metrics before lane-slot expansion."""

    def __init__(self) -> None:
        self._buffers: Dict[Tuple[int, int, int], Deque[np.ndarray]] = {}
        self._seen_timestamps: Dict[Tuple[int, int, int], object] = {}
        self._lock = threading.Lock()

    def push_and_mean(
        self,
        *,
        area_id: int,
        cross_id: int,
        road_id: int,
        values: np.ndarray,
        max_samples: int,
        timestamp: object = None,
    ) -> np.ndarray:
        maxlen = max(1, int(max_samples))
        key = (int(area_id), int(cross_id), int(road_id))
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None or buf.maxlen != maxlen:
                buf = deque(maxlen=maxlen)
                self._buffers[key] = buf

            if timestamp is None or self._seen_timestamps.get(key) != timestamp:
                buf.append(values.astype(np.float32, copy=True))
                if timestamp is not None:
                    self._seen_timestamps[key] = timestamp

            if not buf:
                return values.astype(np.float32, copy=False)
            return np.mean(np.stack(list(buf), axis=0), axis=0).astype(np.float32)

    def clear(self) -> None:
        with self._lock:
            self._buffers.clear()
            self._seen_timestamps.clear()


_metric_history = _RoadMetricHistory()


def clear_road_metric_history() -> None:
    """Test/helper hook."""
    _metric_history.clear()


def _group_roads_by_direction(
    cross: Cross,
    config: Optional[IntersectionConfig],
) -> Tuple[Dict[int, List[Road]], List[int]]:
    """
    Trả về (roads_by_direction, observation_mask_by_direction).

    observation_mask_by_direction[d] = 1 nếu hướng d có ít nhất 1 road.
    """
    groups: Dict[int, List[Road]] = {d: [] for d in range(NUM_DIRECTIONS)}

    direction_map = config.direction_map if config else None

    for road in cross.roads:
        dir_idx: Optional[int] = None

        if direction_map:
            mapped = direction_map.get(str(road.id))
            if mapped is not None and 0 <= mapped < NUM_DIRECTIONS:
                dir_idx = mapped

        if dir_idx is None and road.direction is not None:
            dir_idx = _DB_DIRECTION_MAP.get(road.direction)

        if dir_idx is None:
            continue
        groups[dir_idx].append(road)

    # Fallback round-robin nếu không có road nào được map
    if all(len(v) == 0 for v in groups.values()) and cross.roads:
        for i, road in enumerate(cross.roads):
            groups[i % NUM_DIRECTIONS].append(road)

    direction_has_data = [1 if groups[d] else 0 for d in range(NUM_DIRECTIONS)]
    return groups, direction_has_data


def build_lane_features(
    cross: Cross,
    config: Optional[IntersectionConfig] = None,
    observation_timestamp: object = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Dựng ma trận feature chuẩn hóa cho 12 lane.

    Args:
        cross: request payload.
        config: IntersectionConfig đã load (chứa observation_mask static).
        observation_timestamp: timestamp dùng để tránh push trùng vào rolling history.

    Returns:
        features: shape (12, 4) — lane-major:
                  density, queue, occupancy, speed.
        lane_mask: shape (12,) — 1 nếu lane có data thật, 0 nếu padding.
    """
    groups, _ = _group_roads_by_direction(cross, config)

    feats = np.zeros((TOTAL_LANES, 4), dtype=np.float32)
    feats[:] = DEFAULT_CHANNEL_VALUES
    lane_mask = np.zeros(TOTAL_LANES, dtype=np.float32)
    settings = get_settings()
    observed_length_m = max(float(settings.runtime_observed_length_m), 1.0)
    avg_vehicle_space_m = max(float(settings.runtime_average_vehicle_space_m), 0.1)
    history_samples = max(1, int(settings.runtime_metric_history_samples))
    default_speed_unit = (settings.runtime_average_speed_unit or "km/h").lower()

    def _static_for(road: Road) -> dict:
        return (config.roads_static or {}).get(str(road.id), {}) if config else {}

    def _effective_lanes(static: dict) -> int:
        lanes = static.get("lanes") or static.get("number_of_lanes") or 1
        try:
            return max(1, min(int(float(lanes)), LANES_PER_DIRECTION))
        except (TypeError, ValueError):
            return 1

    def _speed_unit(road: Road) -> str:
        unit = (road.averageSpeedUnit or default_speed_unit or "km/h").lower()
        if unit not in {"m/s", "km/h", "kmh"}:
            logger.warning(
                "[input] averageSpeedUnit=%s invalid for road=%s, defaulting to %s",
                unit,
                road.id,
                default_speed_unit,
            )
            unit = default_speed_unit if default_speed_unit in {"m/s", "km/h", "kmh"} else "km/h"
        return unit

    def _speed_kmh(road: Road, unit: str) -> float:
        raw = float(road.averageSpeed)
        return raw if unit in {"km/h", "kmh"} else raw * 3.6

    def _normalized_default_metrics(road: Road) -> np.ndarray:
        static = _static_for(road)
        lanes = float(_effective_lanes(static))
        speed_design = float(static.get("speed_design_kmh") or 50.0)

        occ_raw = float(road.occupancySpace)
        if occ_raw > 100.0:
            logger.warning(
                "[input] occupancySpace=%s > 100 for road=%s", occ_raw, road.id
            )
        occ_norm = np.clip(occ_raw / 100.0, 0.0, 1.0)

        unit = _speed_unit(road)
        speed_norm = np.clip(_speed_kmh(road, unit) / max(speed_design, 1.0), 0.0, 1.0)

        queue_norm = 0.0
        if road.queueLength is not None:
            queue_norm = np.clip(float(road.queueLength) / observed_length_m, 0.0, 1.0)

        if road.totalVehicle is not None:
            density_norm = (
                float(road.totalVehicle) * avg_vehicle_space_m
                / (observed_length_m * lanes)
            )
        else:
            density_norm = 0.0
        density_norm = np.clip(density_norm, 0.0, 1.0)

        values = np.array(
            [density_norm, queue_norm, occ_norm, speed_norm],
            dtype=np.float32,
        )
        area_id = int(cross.areaId or 0)
        return _metric_history.push_and_mean(
            area_id=area_id,
            cross_id=int(cross.id),
            road_id=int(road.id),
            values=values,
            max_samples=history_samples,
            timestamp=observation_timestamp,
        )

    for dir_idx in range(NUM_DIRECTIONS):
        roads = groups[dir_idx]
        if not roads:
            continue

        lane_offset = 0
        for road in roads:
            if lane_offset >= LANES_PER_DIRECTION:
                break
            static = _static_for(road)
            slots_for_road = _effective_lanes(static)
            values = _normalized_default_metrics(road)
            for _ in range(slots_for_road):
                if lane_offset >= LANES_PER_DIRECTION:
                    break
                lane_idx = dir_idx * LANES_PER_DIRECTION + lane_offset
                feats[lane_idx, :] = values
                lane_mask[lane_idx] = 1.0
                lane_offset += 1

    # Override mask từ config nếu có (snapshot/config quyết định, không phải runtime infer)
    if config is not None and config.observation_mask is not None:
        cfg_mask = np.asarray(config.observation_mask, dtype=np.float32)
        if cfg_mask.shape == (TOTAL_LANES,):
            lane_mask = cfg_mask

    return feats, lane_mask
