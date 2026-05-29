"""
Topology normalization: raw Road list -> ma trận 12 lane x 4 feature chuẩn hóa.

Thứ tự ưu tiên xác định hướng cho mỗi Road:
1. config.direction_map[str(road.id)] (từ GPI offline) — chính xác nhất.
2. road.direction (DB field 1=N, 2=E, 3=S, 4=W) — thường có sẵn.
3. Round-robin theo thứ tự roads — fallback cuối.

Layout output: 12 lane = 4 hướng (N,E,S,W) * 3 lane, mỗi lane 4 feature
(density, queue, occupancy, speed). Thiếu lane thì replicate lane cuối cùng
của hướng; thiếu hướng thì zero + observation_mask=0.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from traffic_rl_features import default_spec

from src.core.logger import logger
from src.preprocessing.feature_builder import FeatureBuilder, get_default_builder
from src.preprocessing.intersection_registry import IntersectionConfig
from src.schemas.common_schemas.cross import Cross
from src.schemas.common_schemas.road import Road


NUM_DIRECTIONS = 4
LANES_PER_DIRECTION = 3
TOTAL_LANES = NUM_DIRECTIONS * LANES_PER_DIRECTION  # 12

# DB direction field (1..4) -> standard index (0..3): 1=N,2=E,3=S,4=W.
# Note: production v_road may also use 8-direction encoding (0=N, 2=E, 4=S,
# 6=W). The authoritative direction inference lives in
# `src/ops/real_normalization.py` which uses cross/road GPS coordinates to
# avoid this ambiguity. This module is only the cold-start fallback when no
# IntersectionConfig has been generated, and we keep the historical 1..4
# mapping here to preserve behavior for existing deployments.
_DB_DIRECTION_MAP = {1: 0, 2: 1, 3: 2, 4: 3}


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
    feature_builder: Optional[FeatureBuilder] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Dựng ma trận feature cho 12 lane bằng formula trong bundle's feature_formula.json.

    Args:
        cross: request payload.
        config: IntersectionConfig đã load (chứa observation_mask static).
        feature_builder: instance đã compile formula. Nếu None -> dùng default
                         (legacy fallback, KHÔNG nên ở production).

    Returns:
        features: shape (C, 12) — C channel (default 4: density, queue, occupancy, speed).
        lane_mask: shape (12,) — 1 nếu lane có data thật, 0 nếu padding.
    """
    builder = feature_builder if feature_builder is not None else get_default_builder()
    num_channels = builder.channel_count
    default_spec_obj = default_spec()
    use_default_formula = (
        builder.spec.channels == default_spec_obj.channels
        and builder.spec.formulas == default_spec_obj.formulas
    )

    groups, _ = _group_roads_by_direction(cross, config)

    feats = np.zeros((num_channels, TOTAL_LANES), dtype=np.float32)
    lane_mask = np.zeros(TOTAL_LANES, dtype=np.float32)

    for dir_idx in range(NUM_DIRECTIONS):
        roads = groups[dir_idx]
        if not roads:
            continue

        for lane_offset in range(LANES_PER_DIRECTION):
            lane_idx = dir_idx * LANES_PER_DIRECTION + lane_offset

            if lane_offset < len(roads):
                road = roads[lane_offset]
                lane_mask[lane_idx] = 1.0
            else:
                # Padding lane: replicate last road, mask vẫn 0.
                road = roads[-1]

            # Normalize runtime inputs to match training distribution.
            static = (config.roads_static or {}).get(str(road.id), {}) if config else {}
            lanes = float(static.get("lanes") or 1.0)
            length_m = float(static.get("length_meters") or 0.0)
            speed_design = float(static.get("speed_design_kmh") or 50.0)

            occ_raw = float(road.occupancySpace)
            if 0.0 <= occ_raw <= 1.0:
                occ_norm = occ_raw
                logger.debug(
                    "[input] occupancySpace seems normalized (0-1) for road=%s", road.id
                )
            else:
                if occ_raw > 100.0:
                    logger.warning(
                        "[input] occupancySpace=%s > 100 for road=%s", occ_raw, road.id
                    )
                occ_norm = np.clip(occ_raw / 100.0, 0.0, 1.0)

            spd_raw = float(road.averageSpeed)
            spd_unit = (road.averageSpeedUnit or "m/s").lower()
            if spd_unit not in {"m/s", "km/h", "kmh"}:
                logger.warning(
                    "[input] averageSpeedUnit=%s invalid for road=%s, defaulting to m/s",
                    spd_unit,
                    road.id,
                )
                spd_unit = "m/s"

            if 0.0 <= spd_raw <= 1.5 and spd_unit == "m/s":
                spd_norm = np.clip(spd_raw, 0.0, 1.0)
                logger.debug(
                    "[input] averageSpeed seems normalized (0-1) for road=%s", road.id
                )
            else:
                if spd_raw > 200.0 and spd_unit in {"km/h", "kmh"}:
                    logger.warning(
                        "[input] averageSpeed=%s too high for road=%s", spd_raw, road.id
                    )
                spd_kmh = spd_raw if spd_unit in {"km/h", "kmh"} else spd_raw * 3.6
                denom = max(speed_design, 1.0)
                spd_norm = np.clip(spd_kmh / denom, 0.0, 1.0)

            queue_norm: Optional[float] = None
            if road.queueLength is not None:
                q = float(road.queueLength)
                if 0.0 <= q <= 1.0 and length_m > 1.0:
                    queue_norm = q
                elif length_m > 0.0:
                    queue_norm = np.clip(q / length_m, 0.0, 1.0)
                else:
                    queue_norm = 0.0

            density_norm: Optional[float] = None
            density_raw: Optional[float] = None
            if road.density is not None:
                density_raw = float(road.density)
            elif road.totalVehicle is not None and road.windowSeconds:
                if road.windowSeconds > 0:
                    flow_vps = float(road.totalVehicle) / float(road.windowSeconds)
                    spd_mps = spd_raw if spd_unit == "m/s" else spd_raw / 3.6
                    if spd_mps > 0.1:
                        density_raw = (flow_vps / spd_mps) * 1000.0  # veh/km
                    else:
                        logger.warning(
                            "[input] speed too low to derive density for road=%s", road.id
                        )

            if density_raw is not None:
                if 0.0 <= density_raw <= 1.0:
                    density_norm = density_raw
                elif lanes > 0.0:
                    # Assume density is vehicles/km; normalize by jam density ~ 1 veh / 7.5m per lane.
                    density_norm = np.clip((density_raw * 7.5) / (lanes * 1000.0), 0.0, 1.0)
                else:
                    density_norm = 0.0

            if use_default_formula:
                # Default spec: use normalized channels directly, fallback to occupancy if missing.
                den_val = occ_norm if density_norm is None else density_norm
                que_val = occ_norm if queue_norm is None else queue_norm
                feats[:, lane_idx] = np.array(
                    [den_val, que_val, occ_norm, spd_norm], dtype=np.float32
                )
            else:
                # Eval N-channel formula. Static vars (lanes, length, ...) tra
                # bundle qua real_road_id. density/queue (normalized) chi duoc dung
                # neu formula co tham chieu.
                channel_values = builder.compute(
                    real_road_id=int(road.id),
                    occupancy=occ_raw,
                    speed=spd_raw if spd_unit in {"km/h", "kmh"} else spd_raw * 3.6,
                    density=density_norm,
                    queue=queue_norm,
                )
                feats[:, lane_idx] = channel_values

    # Override mask từ config nếu có (bundle quyết định, không phải runtime infer)
    if config is not None and config.observation_mask is not None:
        cfg_mask = np.asarray(config.observation_mask, dtype=np.float32)
        if cfg_mask.shape == (TOTAL_LANES,):
            lane_mask = cfg_mask

    return feats, lane_mask
