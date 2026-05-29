"""
Phase normalization: map stage thực của nút giao -> 8 standard phase (FRAP).

Đầu vào chính: config.phase_mapping = list độ dài len(stages), mỗi phần tử là
index standard phase (0..7) tương ứng. Ví dụ [0, 2, 1, 3] nghĩa là stage 0
tương ứng standard phase 0, stage 1 -> phase 2, stage 2 -> phase 1, stage 3 -> phase 3.

Nếu thiếu config.phase_mapping, dùng identity mapping (stage i -> phase i) bị
truncate ở 8.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from src.preprocessing.intersection_registry import IntersectionConfig
from src.schemas.common_schemas.cross import Cross


NUM_STANDARD_PHASES = 8


def _effective_phase_mapping(
    cross: Cross,
    config: Optional[IntersectionConfig],
) -> List[int]:
    """
    Trả mapping độ dài = len(cross.stages), giá trị trong [0, NUM_STANDARD_PHASES).
    Giá trị -1 nghĩa là stage đó không map được (sẽ bị mask ra).

    Thứ tự ưu tiên:
      1. **v2 — cycle/stage-id based** (robust): nếu config.cycles có entry cho
         cross.cycle.id, look up theo stage_id (KHÔNG phụ thuộc thứ tự stage
         trong request).
      2. **v1 — index based** (legacy): config.phase_mapping list theo index.
      3. Fallback identity (chỉ dev/non-strict).
    """
    num_stages = len(cross.stages)

    # V2 path: cycle-aware, stage-id based
    if config is not None and config.cycles is not None:
        cycle_id = getattr(cross.cycle, "id", None) if cross.cycle else None
        stage_to_std = config.stage_to_std_phase_for_cycle(cycle_id)
        if stage_to_std is not None:
            result: List[int] = []
            for stage in cross.stages:
                std = stage_to_std.get(int(stage.id))
                if std is not None and 0 <= std < NUM_STANDARD_PHASES:
                    result.append(int(std))
                else:
                    result.append(-1)
            return result

    # V1 path: index-based mapping
    if config is not None and config.phase_mapping is not None:
        pm = list(config.phase_mapping)
        result: List[int] = []
        for i in range(num_stages):
            if i < len(pm):
                v = int(pm[i])
                result.append(v if 0 <= v < NUM_STANDARD_PHASES else -1)
            else:
                result.append(-1)
        return result

    # Fallback identity. Chỉ chạy khi không có config nào — service strict mode
    # đã chặn trường hợp này ở topology_builder.ensure_area_configs.
    return [i if i < NUM_STANDARD_PHASES else -1 for i in range(num_stages)]


def build_action_mask(
    cross: Cross,
    config: Optional[IntersectionConfig] = None,
) -> np.ndarray:
    """
    Action mask cho 8 standard phase: 1 nếu stage thực có map tới phase đó.
    """
    mask = np.zeros(NUM_STANDARD_PHASES, dtype=np.float32)
    for std_idx in _effective_phase_mapping(cross, config):
        if 0 <= std_idx < NUM_STANDARD_PHASES:
            mask[std_idx] = 1.0
    return mask


def map_stage_actions(
    actions_standard: np.ndarray,
    cross: Cross,
    config: Optional[IntersectionConfig] = None,
    keep_action_index: int = 1,
) -> List[int]:
    """
    Map ngược từ 8 standard actions -> list action cho từng stage thực.

    Trả về list độ dài = len(cross.stages). Stage không map được -> action=keep_action_index
    (giữ nguyên thời lượng đèn xanh hiện tại theo định nghĩa của action space training).
    """
    mapping = _effective_phase_mapping(cross, config)
    out: List[int] = []
    for std_idx in mapping:
        if 0 <= std_idx < NUM_STANDARD_PHASES and std_idx < len(actions_standard):
            out.append(int(actions_standard[std_idx]))
        else:
            out.append(int(keep_action_index))
    return out


def extract_green_time_ratios(cross: Cross) -> np.ndarray:
    """
    Feature phụ 8-dim: tỉ lệ green-time hiện tại / tổng green trong cycle,
    đặt theo thứ tự stage thực (không map qua standard phase — đây là feature
    mô tả phân phối thời gian, không cần FRAP alignment).
    """
    ratios = np.zeros(NUM_STANDARD_PHASES, dtype=np.float32)
    if not cross.stages:
        return ratios

    total_fixed = sum(s.yellow + s.redClear for s in cross.stages)
    total_green = cross.cycle.cycleLength - total_fixed
    if total_green <= 0:
        return ratios

    for i, stage in enumerate(cross.stages):
        if i >= NUM_STANDARD_PHASES:
            break
        green_time = stage.duration - stage.yellow - stage.redClear
        ratios[i] = max(0.0, green_time / total_green)
    return ratios
