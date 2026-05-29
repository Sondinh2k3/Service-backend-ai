"""
Policy manager (ONNX runtime).

Hai layout duoc ho tro:
  1. Bundle layout (Lop 2 — uu tien): policy load tu
     <model_dir>/networks/<network_id>/bundles/<bundle_id>/policy.onnx
     duoc tro toi qua active.json. Cache key = (area_id, bundle_id) — khi
     bundle Active doi, entry cu bi bo.
  2. Legacy layout (backward compat): <model_dir>/area_<id>/policy.onnx, doc
     truc tiep tu disk hoac tu MinIO theo convention cu.

Service chi phu thuoc onnxruntime + numpy o runtime — khong can torch.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import onnxruntime as ort

from src.core.config import get_settings
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.preprocessing.intersection_registry import area_dir, list_areas as list_areas_on_disk
from src.runtime.bundle_resolver import ResolvedBundle, resolve_active_bundle_for_area
from src.services import artifact_storage


DEFAULT_META = {
    "use_local_gnn": True,
    "max_neighbors": 4,
    "obs_dim": 56,
    "obs_stats": None,
    "input_names": ["self_features", "neighbor_features", "neighbor_mask", "neighbor_directions"],
    "output_name": None,  # auto-detect at load time
    # Defaults cho non-windowed, 3-action policy. Phai duoc override boi
    # policy_meta.json cua bundle; o strict mode, missing -> fail fast.
    "window_size": 1,
    "base_obs_dim": 56,
    "num_actions_per_phase": 3,
    "keep_action_index": 1,
}


# Khi strict mode bat, cac key duoi day BAT BUOC phai co trong policy_meta.json.
# Y nghia: bundle training PHAI khai bao day du runtime contract de runtime
# khong silent fall-back vao default sai voi distribution training.
_REQUIRED_META_KEYS: Tuple[str, ...] = (
    "obs_dim",
    "base_obs_dim",
    "window_size",
    "num_actions_per_phase",
    "keep_action_index",
    "input_names",
)


def _validate_meta_contract(area_id: int, meta: dict, strict: bool) -> None:
    """Fail-fast neu meta thieu key bat buoc hoac shape obs_stats sai.

    Cho phep skip required-keys check khi `strict=False` (dev/legacy bundle),
    nhung shape check cua obs_stats luon bat buoc — neu sai shape thi z-score
    se sai semantic, hau qua nghiem trong hon.
    """
    if strict:
        missing = [k for k in _REQUIRED_META_KEYS if k not in meta]
        if missing:
            raise AlgorithmException(
                (
                    f"Area {area_id}: policy_meta.json thieu key bat buoc {missing}. "
                    f"Bundle training phai khai bao day du runtime contract."
                ),
                code=ErrorCode.POLICY_CONTRACT_MISMATCH,
                area_id=area_id,
                extra={"missingMetaKeys": missing},
            )

    obs_dim = int(meta.get("obs_dim", 0))
    base_obs_dim = int(meta.get("base_obs_dim", obs_dim))
    window_size = int(meta.get("window_size", 1))
    if obs_dim <= 0:
        raise AlgorithmException(
            f"Area {area_id}: obs_dim={obs_dim} khong hop le.",
            code=ErrorCode.POLICY_CONTRACT_MISMATCH,
            area_id=area_id,
        )
    if base_obs_dim * window_size != obs_dim:
        raise AlgorithmException(
            (
                f"Area {area_id}: obs_dim={obs_dim} != base_obs_dim*window_size"
                f"={base_obs_dim}*{window_size}={base_obs_dim*window_size}."
            ),
            code=ErrorCode.POLICY_CONTRACT_MISMATCH,
            area_id=area_id,
        )

    num_actions = int(meta.get("num_actions_per_phase", 0))
    keep_idx = int(meta.get("keep_action_index", 0))
    if num_actions < 1:
        raise AlgorithmException(
            f"Area {area_id}: num_actions_per_phase={num_actions} khong hop le.",
            code=ErrorCode.POLICY_CONTRACT_MISMATCH,
            area_id=area_id,
        )
    if not 0 <= keep_idx < num_actions:
        raise AlgorithmException(
            (
                f"Area {area_id}: keep_action_index={keep_idx} ngoai"
                f" [0, {num_actions})."
            ),
            code=ErrorCode.POLICY_CONTRACT_MISMATCH,
            area_id=area_id,
        )

    stats = meta.get("obs_stats")
    if stats and "mean" in stats and "std" in stats:
        mean_len = len(stats["mean"])
        std_len = len(stats["std"])
        if mean_len != obs_dim or std_len != obs_dim:
            raise AlgorithmException(
                (
                    f"Area {area_id}: obs_stats shape mismatch — "
                    f"mean={mean_len}, std={std_len}, expected obs_dim={obs_dim}."
                ),
                code=ErrorCode.POLICY_CONTRACT_MISMATCH,
                area_id=area_id,
            )


@dataclass
class AreaPolicy:
    area_id: int
    session: ort.InferenceSession
    meta: dict
    obs_mean: Optional[np.ndarray]
    obs_std: Optional[np.ndarray]
    input_names: List[str]
    output_name: str
    bundle_id: Optional[str] = None
    network_id: Optional[str] = None
    policy_version: Optional[str] = None
    config_version: Optional[str] = None


_cache: Dict[Tuple[int, Optional[str]], AreaPolicy] = {}
_lock = threading.Lock()


def _legacy_paths(area_id: int) -> Tuple[Path, Path]:
    d = area_dir(area_id)
    return d / "policy.onnx", d / "policy_meta.json"


def _resolve_paths(area_id: int) -> Tuple[Path, Path, Optional[ResolvedBundle]]:
    """Tra ve (policy_path, meta_path, resolved_bundle_or_None)."""
    resolved = resolve_active_bundle_for_area(area_id)
    if resolved is not None:
        return (
            resolved.bundle_path / "policy.onnx",
            resolved.bundle_path / "policy_meta.json",
            resolved,
        )
    onnx_p, meta_p = _legacy_paths(area_id)
    return onnx_p, meta_p, None


def _load_meta(meta_path: Path) -> dict:
    artifact_storage.ensure_local_file(meta_path)
    meta = dict(DEFAULT_META)
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta.update(json.load(f))
        except Exception as e:
            logger.warning(f"Khong doc duoc {meta_path}: {e}. Dung default meta.")
    return meta


def load_policy(area_id: int) -> AreaPolicy:
    onnx_path, meta_path, resolved = _resolve_paths(area_id)
    bundle_id = resolved.bundle_id if resolved else None
    cache_key = (area_id, bundle_id)

    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    artifact_storage.ensure_local_file(onnx_path)
    if not onnx_path.exists():
        raise AlgorithmException(
            f"Khong tim thay policy ONNX cho area={area_id} tai {onnx_path}.",
            code=ErrorCode.POLICY_NOT_FOUND,
            area_id=area_id,
        )

    meta = _load_meta(meta_path)
    _validate_meta_contract(area_id, meta, strict=get_settings().ai_strict_mode)

    logger.info(f"Loading ONNX policy area={area_id} bundle={bundle_id} tu {onnx_path}")
    sess_opts = ort.SessionOptions()
    sess_opts.intra_op_num_threads = 1
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(
        str(onnx_path),
        sess_options=sess_opts,
        providers=["CPUExecutionProvider"],
    )

    input_names = [inp.name for inp in session.get_inputs()]
    output_name = meta.get("output_name") or session.get_outputs()[0].name

    stats = meta.get("obs_stats")
    if stats and "mean" in stats and "std" in stats:
        obs_mean = np.asarray(stats["mean"], dtype=np.float32)
        obs_std = np.asarray(stats["std"], dtype=np.float32)
    else:
        obs_mean = None
        obs_std = None

    policy = AreaPolicy(
        area_id=area_id,
        session=session,
        meta=meta,
        obs_mean=obs_mean,
        obs_std=obs_std,
        input_names=input_names,
        output_name=output_name,
        bundle_id=bundle_id,
        network_id=resolved.network_id if resolved else None,
        policy_version=(
            resolved.pointer.version if resolved else meta.get("policy_version")
        ),
        config_version=meta.get("config_version"),
    )
    with _lock:
        # Don dep entry cu cua cung area (bundle_id khac).
        for k in [k for k in _cache if k[0] == area_id and k[1] != bundle_id]:
            _cache.pop(k, None)
        _cache[cache_key] = policy
    logger.info(
        f"Area {area_id} bundle={bundle_id}: inputs={input_names}, output={output_name}, "
        f"use_local_gnn={meta.get('use_local_gnn')}, max_neighbors={meta.get('max_neighbors')}, "
        f"obs_dim={meta.get('obs_dim')}"
    )
    return policy


def clear_cache(area_id: Optional[int] = None) -> None:
    with _lock:
        if area_id is None:
            _cache.clear()
            return
        for k in [k for k in _cache if k[0] == area_id]:
            _cache.pop(k, None)


def list_available_policies() -> List[Dict]:
    """Liet ke area co artifact tren disk (cho debug / admin)."""
    out: List[Dict] = []
    for aid in list_areas_on_disk():
        onnx_p, meta_p = _legacy_paths(aid)
        out.append({
            "areaId": aid,
            "hasPolicy": onnx_p.exists(),
            "hasMeta": meta_p.exists(),
            "policyPath": str(onnx_p),
        })
    return out
