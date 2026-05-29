"""Sim bundle -> Runtime bundle composer.

Flow:
  1) Extract + validate sim bundle
  2) Compile real_normalization from DB (area_id from area_registry)
  3) Run build-bundle v2 to create runtime bundle ZIP
"""

from __future__ import annotations

import shutil
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from traffic_rl_features import default_spec
from traffic_rl_features.bundle import (
    MANIFEST_FILENAME,
    compute_bundle_checksum,
    compute_dir_checksums,
)

from src.bundles.manifest import ModelManifest
from src.core.config import get_settings
from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session
from src.ops.real_normalization import compile_real_normalization
from src.ops.sim_bundle import (
    SimBundleManifest,
    SimBundleValidationError,
    extract_sim_bundle_zip,
    validate_sim_bundle_dir,
)


class ComposeError(Exception):
    """Composer failure."""


class MissingRealSnapshotError(ComposeError):
    """Sim bundle chua compose duoc vi thieu real_network_snapshot cho network_id.

    Day la loi tam thoi — caller co the giu sim bundle o trang thai
    'pending_real_snapshot' va retry sau khi snapshot duoc upload.
    """


@dataclass
class ComposeResult:
    sim_manifest: SimBundleManifest
    area_id: int
    runtime_zip: Path


def compose_runtime_bundle_from_sim_zip(
    *,
    sim_zip: Path,
    work_dir: Path,
) -> ComposeResult:
    """Build runtime bundle ZIP from sim bundle ZIP.

    Returns ComposeResult with runtime ZIP path.
    """
    settings = get_settings()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    extract_dir = work_dir / f"sim_extract_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        sim_root = extract_sim_bundle_zip(sim_zip, extract_dir)
        sim_manifest = validate_sim_bundle_dir(sim_root)
    except SimBundleValidationError as e:
        raise ComposeError(str(e)) from e

    # Resolve area_id from network_id. Neu chua co area_registry hoac chua co
    # real_network_snapshot -> raise MissingRealSnapshotError (loi tam thoi).
    with get_session() as s:
        area = repo.get_area_by_network(s, sim_manifest.tenant_id, sim_manifest.network_id)
        if area is None:
            raise MissingRealSnapshotError(
                f"Chua co area_registry cho tenant={sim_manifest.tenant_id} "
                f"network={sim_manifest.network_id}. Can register real network snapshot truoc."
            )
        snapshot = repo.get_real_network_snapshot(s, area.area_id)
        if snapshot is None:
            raise MissingRealSnapshotError(
                f"Area {area.area_id} (tenant={sim_manifest.tenant_id} "
                f"network={sim_manifest.network_id}) chua co real_network_snapshot. "
                f"Can goi PUT /internal/sync/areas/{{area_id}}/real-network truoc."
            )
        area_id = area.area_id

    # Compile real normalization
    real_output = work_dir / "real_normalization"
    real_output.mkdir(parents=True, exist_ok=True)
    real_dir = real_output / f"area_{area_id}"
    compile_real_normalization(
        db_url=settings.database_url,
        area_id=area_id,
        output_dir=real_dir,
    )

    real_norm_file = real_dir / "real_normalization.json"
    if not real_norm_file.exists():
        raise ComposeError("real_normalization.json khong duoc tao")

    sim_network = sim_root / sim_manifest.sim_network_path
    policy_onnx = sim_root / sim_manifest.policy_onnx_path
    policy_meta = sim_root / sim_manifest.policy_meta_path

    runtime_zip = work_dir / (
        f"{sim_manifest.network_id}-{sim_manifest.version}-{sim_manifest.sim_bundle_id}.zip"
    )

    deployment_map_file = work_dir / (
        f"deployment_map-{sim_manifest.network_id}-{sim_manifest.sim_bundle_id}.json"
    )
    compatibility_report_file = work_dir / (
        f"compatibility_report-{sim_manifest.network_id}-{sim_manifest.sim_bundle_id}.json"
    )

    deployment_map, report = build_deployment_map_from_real_normalization(
        sim_network_path=sim_network,
        real_normalization_path=real_norm_file,
        area_id=area_id,
        network_id=sim_manifest.network_id,
        created_by="ops-composer",
    )
    deployment_map_file.write_text(
        json.dumps(deployment_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    compatibility_report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if report["summary"]["errors"] > 0:
        raise ComposeError(
            "Compatibility validation fail: "
            f"{report['summary']['errors']} error(s). "
            f"Xem {compatibility_report_file}"
        )

    try:
        from bundle_tooling import build_v2_bundle_zip
    except Exception as e:
        raise ComposeError(
            "Khong import duoc package 'bundle_tooling'. "
            "Can cai traffic-rl-bundle-tooling trong image/container."
        ) from e

    try:
        build_v2_bundle_zip(
            sim_config_path=sim_network,
            deployment_map_path=deployment_map_file,
            policy_onnx_path=policy_onnx,
            policy_meta_path=policy_meta,
            output_zip=runtime_zip,
            tenant_id=sim_manifest.tenant_id,
            version=sim_manifest.version,
            training_run_id=sim_manifest.training_run_id,
            training_dataset_id=sim_manifest.training_dataset_id,
            training_pipeline_commit=sim_manifest.training_pipeline_commit,
            commissioned_by="ops-composer",
            extras={
                "source_sim_bundle_id": sim_manifest.sim_bundle_id,
                "source_sim_bundle_schema_version": sim_manifest.schema_version,
                "sim_network_path": "sim_network.json",
                "real_normalization_path": "real_normalization.json",
                "compatibility_report_path": "compatibility_report.json",
            },
            strict=True,
        )
    except Exception as e:
        raise ComposeError(f"build runtime bundle fail: {e}") from e

    _enrich_runtime_bundle_zip(
        runtime_zip=runtime_zip,
        sim_network_path=sim_network,
        real_normalization_path=real_norm_file,
        compatibility_report_path=compatibility_report_file,
        sim_bundle_id=sim_manifest.sim_bundle_id,
    )

    logger.info(
        f"[composer] Built runtime bundle {runtime_zip} "
        f"(network={sim_manifest.network_id}, version={sim_manifest.version})"
    )

    # Cleanup extracted sim files to keep work_dir small
    try:
        shutil.rmtree(extract_dir, ignore_errors=True)
    except Exception:
        pass

    return ComposeResult(sim_manifest=sim_manifest, area_id=area_id, runtime_zip=runtime_zip)


_DIR_TO_IDX = {"N": 0, "E": 1, "S": 2, "W": 3}


def build_deployment_map_from_real_normalization(
    *,
    sim_network_path: Path,
    real_normalization_path: Path,
    area_id: int,
    network_id: str,
    created_by: str,
) -> Tuple[dict, dict]:
    """Generate deployment_map.json from sim_network + DB real_normalization.

    This is the service-side bridge requested by the pipeline:
    sim_network.json stays the training contract, real_normalization.json is
    compiled from DB, and this generated deployment_map is the exact artifact
    consumed by bundle-tooling validation/build.
    """
    sim_network = json.loads(Path(sim_network_path).read_text(encoding="utf-8"))
    real_norm = json.loads(Path(real_normalization_path).read_text(encoding="utf-8"))

    sim_intersections: Dict[str, dict] = sim_network.get("intersections") or {}
    if not sim_intersections:
        raise ComposeError("sim_network.json phai co key 'intersections'.")
    real_crosses: List[dict] = list(real_norm.get("crosses") or [])
    if not real_crosses:
        raise ComposeError("real_normalization.json khong co crosses.")

    sim_ids = list(sim_intersections.keys())
    sim_to_real, mapping_warnings = _resolve_sim_to_real_mapping(
        sim_ids=sim_ids,
        real_crosses=real_crosses,
        real_norm=real_norm,
    )

    crosses = []
    errors: List[dict] = []
    warnings: List[dict] = list(mapping_warnings)

    real_by_id = {int(c["real_cross_id"]): c for c in real_crosses}
    for sim_id in sim_ids:
        real_id = sim_to_real.get(sim_id)
        if real_id is None:
            errors.append({"type": "SIM_CROSS_NOT_MAPPED", "sim_tls_id": sim_id})
            continue
        sim_cross = sim_intersections[sim_id]
        real_cross = real_by_id.get(int(real_id))
        if real_cross is None:
            errors.append({
                "type": "REAL_CROSS_NOT_FOUND",
                "sim_tls_id": sim_id,
                "real_cross_id": real_id,
            })
            continue
        try:
            crosses.append(_build_cross_mapping(sim_id, sim_cross, real_cross))
        except ComposeError as e:
            errors.append({
                "type": "CROSS_COMPATIBILITY_ERROR",
                "sim_tls_id": sim_id,
                "real_cross_id": real_id,
                "message": str(e),
            })

    feature_spec = default_spec().to_dict()
    deployment_map = {
        "schema_version": "1.0",
        "area_id": area_id,
        "network_id": network_id,
        "sim_config_path": "sim_network.json",
        "sim_config_sha256": None,
        "feature_formula": feature_spec,
        "crosses": crosses,
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notes": (
            "Auto-generated by ai-ops from sim_network.json + "
            "real_normalization.json."
        ),
    }

    tooling_issues = []
    if not errors:
        try:
            from bundle_tooling.deployment_map import DeploymentMap
            from bundle_tooling.deployment_validator import (
                validate as validate_deployment_map,
            )

            dm_obj = DeploymentMap.model_validate(deployment_map)
            for issue in validate_deployment_map(dm_obj, sim_network):
                item = {
                    "severity": issue.severity.value,
                    "sim_tls_id": issue.sim_tls_id,
                    "type": issue.code,
                    "message": issue.message,
                }
                tooling_issues.append(item)
                if issue.severity.value == "error":
                    errors.append(item)
                else:
                    warnings.append(item)
        except Exception as e:
            errors.append({
                "type": "DEPLOYMENT_MAP_VALIDATION_EXCEPTION",
                "message": str(e),
            })

    report = {
        "errors": errors,
        "warnings": warnings,
        "tooling_issues": tooling_issues,
        "summary": {
            "sim_crosses": len(sim_ids),
            "real_crosses": len(real_crosses),
            "mapped_crosses": len(crosses),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }
    return deployment_map, report


def _resolve_sim_to_real_mapping(
    *,
    sim_ids: List[str],
    real_crosses: List[dict],
    real_norm: dict,
) -> Tuple[Dict[str, int], List[dict]]:
    explicit = (
        real_norm.get("sim_to_real")
        or real_norm.get("cross_map")
        or real_norm.get("sim_to_real_crosses")
    )
    warnings: List[dict] = []
    if isinstance(explicit, dict) and explicit:
        return {str(k): int(v) for k, v in explicit.items()}, warnings

    by_sim_key: Dict[str, int] = {}
    for c in real_crosses:
        sim_key = c.get("sim_tls_id") or c.get("sim_cross_id")
        if sim_key:
            by_sim_key[str(sim_key)] = int(c["real_cross_id"])
    if by_sim_key:
        return by_sim_key, warnings

    if len(sim_ids) != len(real_crosses):
        raise ComposeError(
            "Khong co sim_to_real mapping va so luong sim/real cross khong khop: "
            f"sim={len(sim_ids)} real={len(real_crosses)}."
        )

    warnings.append({
        "type": "AUTO_CROSS_MAPPING_BY_ORDER",
        "message": (
            "real_normalization.json khong co sim_to_real mapping; composer map "
            "theo thu tu sim_network.intersections va real_normalization.crosses. "
            "Production nen cung cap mapping explicit."
        ),
    })
    return {
        sim_id: int(real_crosses[i]["real_cross_id"])
        for i, sim_id in enumerate(sim_ids)
    }, warnings


def _build_cross_mapping(sim_id: str, sim_cross: dict, real_cross: dict) -> dict:
    roads_by_direction = {}
    sim_dir_map = sim_cross.get("direction_map") or {}
    sim_lanes_by_dir = sim_cross.get("lanes_by_direction") or {}
    real_road_by_dir = _real_road_by_direction(real_cross)
    roads_static = real_cross.get("roads_static") or {}

    for direction in ("N", "E", "S", "W"):
        sim_edge_id = sim_dir_map.get(direction)
        real_road_id = real_road_by_dir.get(_DIR_TO_IDX[direction])
        if sim_edge_id is None:
            roads_by_direction[direction] = None
            continue
        if real_road_id is None:
            raise ComposeError(
                f"Huong {direction}: sim co edge {sim_edge_id!r} "
                "nhung real_normalization khong co road tuong ung."
            )
        props = roads_static.get(str(real_road_id)) or {}
        sim_lanes = len(sim_lanes_by_dir.get(direction) or [])
        roads_by_direction[direction] = {
            "sim_edge_id": str(sim_edge_id),
            "real_road_id": int(real_road_id),
            "real_lanes": int(props.get("lanes") or max(sim_lanes, 1)),
            "sim_lanes": sim_lanes or None,
            "length_meters": props.get("length_meters"),
            "speed_design_kmh": props.get("speed_design_kmh"),
            "saturation_flow": props.get("saturation_flow"),
        }

    cycles = [_build_primary_cycle_mapping(sim_cross, real_cross)]
    return {
        "sim_tls_id": str(sim_id),
        "real_cross_id": int(real_cross["real_cross_id"]),
        "roads_by_direction": roads_by_direction,
        "cycles": cycles,
    }


def _real_road_by_direction(real_cross: dict) -> Dict[int, int]:
    """Invert direction_map ({road_id_str: dir_idx}) into {dir_idx: road_id}.

    real_normalization.compile_real_normalization now guarantees at most one
    road per direction via the GPI tiebreaker, but legacy snapshots (compiled
    before that fix) may still contain multiple entries per direction. Pick the
    smallest road_id deterministically so the same snapshot always produces the
    same deployment_map, instead of relying on dict iteration order.
    """
    grouped: Dict[int, List[int]] = {}
    for road_id, dir_idx in (real_cross.get("direction_map") or {}).items():
        grouped.setdefault(int(dir_idx), []).append(int(road_id))
    return {d: min(rids) for d, rids in grouped.items()}


def _build_primary_cycle_mapping(sim_cross: dict, real_cross: dict) -> dict:
    phase_cfg = sim_cross.get("phase_config") or {}
    sim_phases = list(phase_cfg.get("phases") or [])
    if not sim_phases:
        raise ComposeError("sim cross khong co phase_config.phases.")

    primary_cycle_id = real_cross.get("primary_cycle_id")
    cycles = real_cross.get("cycles") or {}
    if primary_cycle_id is None or str(primary_cycle_id) not in cycles:
        raise ComposeError("real cross khong co primary cycle trong real_normalization.")
    primary = cycles[str(primary_cycle_id)]
    stage_to_std = primary.get("stage_to_standard_phase") or {}
    real_stage_ids = [int(k) for k in stage_to_std.keys()]
    if len(real_stage_ids) != len(sim_phases):
        raise ComposeError(
            "stage count mismatch: "
            f"sim_phases={len(sim_phases)} real_stages={len(real_stage_ids)}."
        )

    ordinal_to_std = {}
    for k, v in (phase_cfg.get("actual_to_standard") or {}).items():
        try:
            ordinal_to_std[int(k)] = int(v)
        except (TypeError, ValueError):
            continue

    phase_to_stage = []
    for ordinal, phase in enumerate(sim_phases):
        sim_phase_idx = int(phase.get("index", ordinal))
        std_phase_idx = int(ordinal_to_std.get(ordinal, ordinal if ordinal < 8 else 7))
        phase_to_stage.append({
            "sim_phase_idx": sim_phase_idx,
            "real_stage_id": int(real_stage_ids[ordinal]),
            "std_phase_idx": std_phase_idx,
        })

    return {
        "real_cycle_id": int(primary_cycle_id),
        "is_primary": True,
        "phase_to_stage": phase_to_stage,
    }


def _enrich_runtime_bundle_zip(
    *,
    runtime_zip: Path,
    sim_network_path: Path,
    real_normalization_path: Path,
    compatibility_report_path: Path,
    sim_bundle_id: str,
) -> None:
    """Add audit artifacts to runtime bundle and recompute manifest checksum."""
    enrich_root = runtime_zip.parent / f"_enrich_{runtime_zip.stem}"
    if enrich_root.exists():
        shutil.rmtree(enrich_root)
    enrich_root.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(runtime_zip, "r") as zf:
            zf.extractall(enrich_root)

        shutil.copy2(sim_network_path, enrich_root / "sim_network.json")
        shutil.copy2(real_normalization_path, enrich_root / "real_normalization.json")
        shutil.copy2(compatibility_report_path, enrich_root / "compatibility_report.json")

        manifest_path = enrich_root / MANIFEST_FILENAME
        manifest = ModelManifest.from_dict(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        extras = dict(manifest.extras or {})
        extras.update({
            "source_sim_bundle_id": sim_bundle_id,
            "sim_network_path": "sim_network.json",
            "real_normalization_path": "real_normalization.json",
            "compatibility_report_path": "compatibility_report.json",
        })
        manifest.extras = extras
        manifest.file_checksums = compute_dir_checksums(
            enrich_root, exclude=(MANIFEST_FILENAME,)
        )
        manifest.checksum = compute_bundle_checksum(manifest.file_checksums)
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with zipfile.ZipFile(runtime_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(enrich_root.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(enrich_root).as_posix())
    finally:
        shutil.rmtree(enrich_root, ignore_errors=True)
