"""Runtime feature builder — thin wrapper trên shared package `traffic_rl_features`.

Core compute engine (`FeatureBuilder`, `FeatureSpec`) đến từ shared package để
sim trainer + service cùng import → cùng kết quả. Module này thêm 2 thứ
service-specific:
  - Service-level cache theo (area_id, bundle_id)
  - Load `feature_formula.json` từ bundle directory, merge `roads_static` từ
    nhiều cross config trong cùng area.

Khi shared package bump major version, runtime check qua manifest.feature_pkg_version
(Bước tương lai).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from traffic_rl_features import (
    FeatureBuilder,
    FeatureSpec,
    FormulaError,
    PACKAGE_VERSION,
    default_spec,
)

from src.core.logger import logger


# Re-export defaults từ shared package để code cũ trong service không gãy.
from traffic_rl_features import (  # noqa: E402  (re-export)
    DEFAULT_ROAD_LANES,
    DEFAULT_ROAD_LENGTH_M,
    DEFAULT_ROAD_SATURATION_FLOW,
    DEFAULT_ROAD_SPEED_DESIGN_KMH,
)


_cache: Dict[tuple, FeatureBuilder] = {}


def compile_feature_formula(formula_dict: dict) -> FeatureSpec:
    """Parse formula dict (từ bundle's feature_formula.json) → FeatureSpec.

    Giữ tên cũ cho backward compat. Code mới nên dùng `FeatureSpec.from_dict`.
    Raises `FormulaError` nếu syntax/biến không hợp lệ.
    """
    return FeatureSpec.from_dict(formula_dict)


def get_default_builder() -> FeatureBuilder:
    """Fallback builder — dùng default spec, KHÔNG có roads_static.

    Chỉ phù hợp dev/test hoặc legacy bundle. Production luôn nên dùng spec từ
    bundle + roads_static đầy đủ.
    """
    return FeatureBuilder(spec=default_spec(), roads_static=None)


def build_from_bundle(
    bundle_root: Path,
    cross_configs: Dict[int, dict],
    cache_key: tuple,
) -> FeatureBuilder:
    """Build (hoặc cache hit) FeatureBuilder cho 1 bundle.

    Args:
        bundle_root: thư mục bundle đã extract (chứa `feature_formula.json`).
        cross_configs: map real_cross_id → cross config dict đã load. Dùng để
                       merge `roads_static` từ mọi cross trong area thành dict
                       chung cho FeatureBuilder.
        cache_key: vd (area_id, bundle_id). Khác bundle ⇒ khác key ⇒ rebuild.
    """
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    ff_path = bundle_root / "feature_formula.json"
    spec: FeatureSpec
    if ff_path.exists():
        try:
            with open(ff_path, "r", encoding="utf-8") as f:
                formula_dict = json.load(f)
            spec = FeatureSpec.from_dict(formula_dict)
        except (json.JSONDecodeError, OSError, FormulaError) as e:
            logger.warning(
                f"[feature_builder] Lỗi load {ff_path}: {e}. Dùng default spec."
            )
            spec = default_spec()
    else:
        logger.info(
            f"[feature_builder] {bundle_root} không có feature_formula.json — "
            f"dùng default spec (legacy bundle)."
        )
        spec = default_spec()

    # Merge roads_static từ mọi cross trong area.
    roads_static: Dict[str, dict] = {}
    for cfg in cross_configs.values():
        rs = cfg.get("roads_static") or {}
        for rid, props in rs.items():
            roads_static[str(rid)] = props

    builder = FeatureBuilder(spec=spec, roads_static=roads_static)
    _cache[cache_key] = builder
    logger.info(
        f"[feature_builder] Built cho key={cache_key}: "
        f"channels={spec.channels}, roads_static_keys={len(roads_static)}, "
        f"pkg_version={PACKAGE_VERSION}"
    )
    return builder


def clear_cache(cache_key: Optional[tuple] = None) -> None:
    """Xóa cache. Gọi khi bundle activate mới."""
    if cache_key is None:
        _cache.clear()
        return
    _cache.pop(cache_key, None)
