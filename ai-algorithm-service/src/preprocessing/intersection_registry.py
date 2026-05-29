"""
Intersection + area config registry.

Cấu trúc thư mục (area-scoped):
    <model_dir>/area_<areaId>/policy.onnx
    <model_dir>/area_<areaId>/policy_meta.json    # hyperparams + obs_stats (export từ training)
    <model_dir>/area_<areaId>/network.json        # neighbor graph giữa các cross
    <model_dir>/area_<areaId>/intersections/cross_<crossId>.json

Per-cross config (auto-gen lần đầu, cache disk):
{
  "cross_id": 123,
  "direction_map": {"101": 0, "102": 1, ...},   # road_id -> direction idx 0..3
  "phase_mapping": [0, 2, 1, 3],                 # stage idx -> standard phase idx 0..7
  "observation_mask": [1,1,1,0,...]              # 12 lanes
}

obs_stats KHÔNG còn per-cross — được bundle cùng policy ở `policy_meta.json` vì
phải khớp với distribution training.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.core.config import get_settings
from src.core.logger import logger
from src.services import artifact_storage


@dataclass
class IntersectionConfig:
    """Per-cross config đọc từ bundle's intersections/cross_<id>.json.

    Hỗ trợ cả v1 (legacy phase_mapping list theo index) và v2 (cycles dict
    keyed cycle_id, mỗi cycle có stage_to_standard_phase map theo stage_id).
    v2 robust hơn vì stage_id ổn định khi reorder, và cycle_id cho phép DB
    switch cycle (gốc/con/làn sóng xanh) mà mapping vẫn đúng.
    """

    cross_id: int
    direction_map: Dict[str, int] = field(default_factory=dict)
    # Legacy v1: list[std_phase_idx] theo thứ tự stage trong cross.stages.
    phase_mapping: Optional[List[int]] = None
    observation_mask: Optional[List[int]] = None

    # v2 fields. None nếu là legacy bundle.
    primary_cycle_id: Optional[int] = None
    # cycles[cycle_id_str] = {
    #   "stage_to_standard_phase": {stage_id_str: std_idx},
    #   "standard_phase_to_stage": {std_idx_str: stage_id},
    #   "is_primary": bool, "num_stages": int,
    # }
    cycles: Optional[Dict[str, dict]] = None
    # roads_static[real_road_id_str] = {lanes, length_meters, speed_design_kmh,
    # saturation_flow}. Dùng bởi FeatureBuilder.
    roads_static: Optional[Dict[str, dict]] = None

    @classmethod
    def from_dict(cls, data: dict) -> "IntersectionConfig":
        return cls(
            cross_id=int(data.get("cross_id", 0)),
            direction_map={str(k): int(v) for k, v in (data.get("direction_map") or {}).items()},
            phase_mapping=list(data["phase_mapping"]) if data.get("phase_mapping") is not None else None,
            observation_mask=list(data["observation_mask"]) if data.get("observation_mask") is not None else None,
            primary_cycle_id=(
                int(data["primary_cycle_id"]) if data.get("primary_cycle_id") is not None else None
            ),
            cycles=dict(data["cycles"]) if data.get("cycles") else None,
            roads_static=dict(data["roads_static"]) if data.get("roads_static") else None,
        )

    def to_dict(self) -> dict:
        out: dict = {"cross_id": self.cross_id}
        if self.direction_map:
            out["direction_map"] = self.direction_map
        if self.phase_mapping is not None:
            out["phase_mapping"] = self.phase_mapping
        if self.observation_mask is not None:
            out["observation_mask"] = self.observation_mask
        if self.primary_cycle_id is not None:
            out["primary_cycle_id"] = self.primary_cycle_id
        if self.cycles is not None:
            out["cycles"] = self.cycles
        if self.roads_static is not None:
            out["roads_static"] = self.roads_static
        return out

    def stage_to_std_phase_for_cycle(
        self, cycle_id: Optional[int]
    ) -> Optional[Dict[int, int]]:
        """Trả map stage_id (int) → std_phase_idx (int) cho cycle yêu cầu.

        Nếu cycle_id None hoặc không có bundle → fallback primary_cycle_id.
        Nếu cả primary cũng None (legacy v1) → None.
        """
        if self.cycles is None:
            return None
        cid: Optional[int] = cycle_id
        if cid is None or str(cid) not in self.cycles:
            cid = self.primary_cycle_id
        if cid is None or str(cid) not in self.cycles:
            return None
        raw = (self.cycles[str(cid)].get("stage_to_standard_phase") or {})
        return {int(k): int(v) for k, v in raw.items()}


_cache: Dict[tuple, Optional[IntersectionConfig]] = {}


def models_root() -> Path:
    settings = get_settings()
    model_dir = Path(settings.model_dir)
    if not model_dir.is_absolute():
        model_dir = Path.cwd() / model_dir
    return model_dir


def area_dir(area_id: int) -> Path:
    return models_root() / f"area_{area_id}"


def _bundle_root_for_area(area_id: int) -> Optional[Path]:
    """Path bundle hien tai cua area neu co (uu tien hon legacy)."""
    try:
        # Import lazy de tranh circular import voi src.runtime.* cua ai-ops bo prune.
        from src.runtime.bundle_resolver import resolve_active_bundle_for_area
    except Exception:
        return None
    resolved = resolve_active_bundle_for_area(area_id)
    if resolved is None:
        return None
    return resolved.bundle_path


def bundle_root_for_area(area_id: int) -> Optional[Path]:
    """Public alias cho dùng ngoài module."""
    return _bundle_root_for_area(area_id)


def _config_path(area_id: int, cross_id: int) -> Path:
    bundle = _bundle_root_for_area(area_id)
    if bundle is not None:
        candidate = bundle / "intersections" / f"cross_{cross_id}.json"
        if candidate.exists():
            return candidate
    return area_dir(area_id) / "intersections" / f"cross_{cross_id}.json"


def get_config(area_id: int, cross_id: int) -> Optional[IntersectionConfig]:
    key = (area_id, cross_id)
    if key in _cache:
        return _cache[key]

    path = _config_path(area_id, cross_id)
    if not path.exists():
        _cache[key] = None
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = IntersectionConfig.from_dict(data)
        _cache[key] = cfg
        return cfg
    except Exception as e:
        logger.error(f"Lỗi load config {path}: {e}")
        _cache[key] = None
        return None


def save_config(area_id: int, cfg: IntersectionConfig) -> Path:
    path = _config_path(area_id, cfg.cross_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
    _cache[(area_id, cfg.cross_id)] = cfg
    artifact_storage.upload_local_file(path)
    logger.info(f"Đã ghi intersection config area={area_id} cross={cfg.cross_id} tại {path}")
    return path


def clear_cache(area_id: Optional[int] = None) -> None:
    if area_id is None:
        _cache.clear()
        return
    for k in list(_cache.keys()):
        if k[0] == area_id:
            _cache.pop(k, None)


def load_network(area_id: int) -> Optional[dict]:
    bundle = _bundle_root_for_area(area_id)
    if bundle is not None:
        path = bundle / "network.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Lỗi load network {path}: {e}")

    path = area_dir(area_id) / "network.json"
    artifact_storage.ensure_local_file(path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Lỗi load network {path}: {e}")
        return None


def save_network(area_id: int, network: dict) -> Path:
    path = area_dir(area_id) / "network.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(network, f, ensure_ascii=False, indent=2)
    artifact_storage.upload_local_file(path)
    logger.info(f"Đã ghi network area={area_id} tại {path}")
    return path


def list_areas() -> List[int]:
    root = models_root()
    if not root.exists():
        return []
    ids: List[int] = []
    for p in root.iterdir():
        if p.is_dir() and p.name.startswith("area_"):
            try:
                ids.append(int(p.name[len("area_"):]))
            except ValueError:
                continue
    return sorted(ids)
