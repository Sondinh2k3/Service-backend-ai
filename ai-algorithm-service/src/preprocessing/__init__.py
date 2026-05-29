"""
Preprocessing package: chuẩn hóa topology + phase + features trước inference.

Area-scoped: mọi config được gắn với areaId của policy tương ứng. Lần đầu thấy
area mới, `topology_builder.ensure_area_configs()` auto-generate từ dữ liệu
request và ghi xuống disk.
"""

from src.preprocessing.intersection_registry import (
    IntersectionConfig,
    area_dir,
    clear_cache,
    get_config,
    list_areas,
    load_network,
    save_config,
    save_network,
)
from src.preprocessing.topology_normalizer import build_lane_features
from src.preprocessing.phase_normalizer import (
    build_action_mask,
    map_stage_actions,
    extract_green_time_ratios,
)
from src.preprocessing.feature_normalizer import FeatureNormalizer
from src.preprocessing.observation_history import (
    ObservationHistory,
    get_observation_history,
)
from src.preprocessing.topology_builder import (
    MAX_NEIGHBORS,
    area_policy_paths,
    ensure_area_configs,
    get_neighbor_ids,
)

__all__ = [
    "IntersectionConfig",
    "area_dir",
    "area_policy_paths",
    "build_action_mask",
    "build_lane_features",
    "clear_cache",
    "ensure_area_configs",
    "extract_green_time_ratios",
    "FeatureNormalizer",
    "get_config",
    "get_neighbor_ids",
    "get_observation_history",
    "list_areas",
    "ObservationHistory",
    "load_network",
    "map_stage_actions",
    "MAX_NEIGHBORS",
    "save_config",
    "save_network",
]
