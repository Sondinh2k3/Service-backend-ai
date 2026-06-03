"""Sync service (plan 5.2, 6.1.3).

Cac API `/internal/sync/*` goi sang day. Moi request co `source_event_id` ->
idempotent: neu da xu ly -> return previous result, khong re-apply.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session
from src.preprocessing.intersection_registry import (
    IntersectionConfig,
    area_dir,
    clear_cache as clear_intersection_config_cache,
    save_config,
)
from src.services.readiness_service import check_area
from src.services import model_manager
from src.services import artifact_storage


def _hash_payload(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _ensure_idempotent(session, *, source_event_id: str, event_type: str, payload: Dict[str, Any]) -> Optional[dict]:
    """Tra ve dict {status:'duplicate', ...} neu da xu ly truoc do, else None."""
    existing = repo.get_sync_event(session, source_event_id)
    if existing is None:
        return None
    new_hash = _hash_payload(payload)
    if existing.payload_hash != new_hash:
        raise AlgorithmException(
            f"source_event_id={source_event_id} da duoc ghi voi payload khac.",
            code=ErrorCode.SYNC_IDEMPOTENCY_CONFLICT,
        )
    return {"status": "duplicate", "sourceEventId": source_event_id, "eventType": existing.event_type}


# ---------------------------------------------------------------------------
# API-level operations
# ---------------------------------------------------------------------------

def upsert_area(
    *,
    area_id: int,
    area_name: str,
    is_active: bool,
    controller_visible: bool,
    tenant_id: Optional[str],
    network_id: Optional[str],
    source_event_id: str,
) -> dict:
    payload = {
        "area_id": area_id,
        "area_name": area_name,
        "is_active": is_active,
        "controller_visible": controller_visible,
        "tenant_id": tenant_id,
        "network_id": network_id,
    }
    with get_session() as session:
        dup = _ensure_idempotent(
            session,
            source_event_id=source_event_id,
            event_type="area.upsert",
            payload=payload,
        )
        if dup:
            return dup

        repo.upsert_area(
            session,
            area_id=area_id,
            area_name=area_name,
            is_active=is_active,
            controller_visible=controller_visible,
            tenant_id=tenant_id,
            network_id=network_id,
        )
        repo.record_sync_event(
            session,
            source_event_id=source_event_id,
            event_type="area.upsert",
            payload_hash=_hash_payload(payload),
        )
    logger.info(
        f"Sync area upsert areaId={area_id} active={is_active} "
        f"tenant={tenant_id or 'default'} network={network_id or f'area_{area_id}'}"
    )
    return {
        "status": "applied",
        "areaId": area_id,
        "tenantId": tenant_id or "default",
        "networkId": network_id or f"area_{area_id}",
    }


def upsert_artifact(
    *,
    area_id: int,
    policy_version: str,
    config_version: str,
    policy_path: str,
    meta_path: str,
    network_path: Optional[str],
    checksum: Optional[str],
    activate: bool,
    source_event_id: str,
) -> dict:
    payload = {
        "area_id": area_id,
        "policy_version": policy_version,
        "config_version": config_version,
        "policy_path": policy_path,
        "meta_path": meta_path,
        "network_path": network_path,
        "checksum": checksum,
        "activate": activate,
    }
    artifact_id: Optional[int] = None
    with get_session() as session:
        dup = _ensure_idempotent(
            session,
            source_event_id=source_event_id,
            event_type="artifact.upsert",
            payload=payload,
        )
        if dup:
            return dup

        # Area phai ton tai truoc.
        area = repo.get_area(session, area_id)
        if area is None:
            raise AlgorithmException(
                f"Area {area_id} chua duoc khai bao, hay upsert area truoc.",
                code=ErrorCode.AREA_NOT_FOUND,
                area_id=area_id,
            )

        art = repo.upsert_artifact(
            session,
            area_id=area_id,
            policy_version=policy_version,
            config_version=config_version,
            policy_path=policy_path,
            meta_path=meta_path,
            network_path=network_path,
            checksum=checksum,
            status="invalid",
        )
        if activate:
            repo.activate_artifact(session, art.id)
            # Cache ONNX co the cu -> clear de load lai khi inference sau.
            model_manager.clear_cache(area_id)

        artifact_id = art.id

        repo.record_sync_event(
            session,
            source_event_id=source_event_id,
            event_type="artifact.upsert",
            payload_hash=_hash_payload(payload),
        )
    logger.info(
        f"Sync artifact area={area_id} v={policy_version}/{config_version} activate={activate}"
    )

    base_dir = area_dir(area_id)
    policy_local = artifact_storage.resolve_local_path(policy_path, base_dir / "policy.onnx")
    meta_local = artifact_storage.resolve_local_path(meta_path, base_dir / "policy_meta.json")
    network_local = (
        artifact_storage.resolve_local_path(network_path, base_dir / "network.json")
        if network_path is not None
        else None
    )

    artifact_storage.upload_local_file(policy_local, policy_path)
    artifact_storage.upload_local_file(meta_local, meta_path)
    if network_local is not None:
        artifact_storage.upload_local_file(network_local, network_path)

    return {
        "status": "applied",
        "areaId": area_id,
        "artifactId": artifact_id,
        "activated": activate,
    }


def sync_cross_config(
    *,
    area_id: int,
    cross_id: int,
    config_payload: Dict[str, Any],
    config_version: str,
    source_event_id: str,
    write_file: bool = True,
) -> dict:
    """Dong bo config cross (direction_map, phase_mapping, observation_mask).

    Plan 5.2.3: luu vao DB va file runtime.
    """
    payload = {
        "area_id": area_id,
        "cross_id": cross_id,
        "config_version": config_version,
        "config_payload": config_payload,
    }
    with get_session() as session:
        dup = _ensure_idempotent(
            session,
            source_event_id=source_event_id,
            event_type="cross_config.upsert",
            payload=payload,
        )
        if dup:
            return dup

        area = repo.get_area(session, area_id)
        if area is None:
            raise AlgorithmException(
                f"Area {area_id} chua duoc khai bao.",
                code=ErrorCode.AREA_NOT_FOUND,
                area_id=area_id,
            )

        cfg_json = json.dumps(config_payload, ensure_ascii=False, sort_keys=True)
        row = repo.upsert_cross_config(
            session,
            area_id=area_id,
            cross_id=cross_id,
            payload_json=cfg_json,
            config_version=config_version,
            checksum=hashlib.sha256(cfg_json.encode("utf-8")).hexdigest(),
        )
        repo.record_sync_event(
            session,
            source_event_id=source_event_id,
            event_type="cross_config.upsert",
            payload_hash=_hash_payload(payload),
        )

    # Ghi file runtime ngoai transaction (disk I/O).
    if write_file:
        cfg_struct = dict(config_payload)
        cfg_struct.setdefault("cross_id", cross_id)
        try:
            save_config(area_id, IntersectionConfig.from_dict(cfg_struct))
        except Exception as e:
            logger.warning(f"Khong ghi duoc file cross config area={area_id} cross={cross_id}: {e}")

    logger.info(f"Sync cross_config area={area_id} cross={cross_id} v={config_version}")
    return {"status": "applied", "areaId": area_id, "crossId": cross_id, "rowId": row.id}


def sync_real_network_snapshot(
    *,
    area_id: int,
    tenant_id: Optional[str],
    network_id: Optional[str],
    schema_version: str,
    source_version: Optional[str],
    area: Dict[str, Any],
    area_crosses: List[Dict[str, Any]],
    crosses: List[Dict[str, Any]],
    roads: List[Dict[str, Any]],
    cycles: List[Dict[str, Any]],
    stages: List[Dict[str, Any]],
    sim_to_real: Dict[str, Any],
    source_event_id: str,
) -> dict:
    """Luu snapshot mang thuc vao DB noi bo cua AI service.

    Payload nay la diem vao chinh cho flow sim-to-real: backend/controller chon
    area + cac nut giao, gui du lieu sang service; service dung snapshot nay de
    sinh `real_normalization.json` khi co Sim Bundle moi.
    """
    effective_tenant_id = tenant_id or "default"
    effective_network_id = network_id or f"area_{area_id}"
    normalized_area = dict(area or {})
    normalized_area.setdefault("area_id", area_id)

    payload = {
        "area_id": area_id,
        "tenant_id": effective_tenant_id,
        "network_id": effective_network_id,
        "schema_version": schema_version,
        "source_version": source_version,
        "area": normalized_area,
        "area_crosses": list(area_crosses or []),
        "crosses": list(crosses or []),
        "roads": list(roads or []),
        "cycles": list(cycles or []),
        "stages": list(stages or []),
        "sim_to_real": dict(sim_to_real or {}),
    }

    if not payload["area_crosses"]:
        raise AlgorithmException("Real network snapshot thieu areaCrosses.", code=ErrorCode.INVALID_INPUT)
    if not payload["crosses"]:
        raise AlgorithmException("Real network snapshot thieu crosses.", code=ErrorCode.INVALID_INPUT)
    if not payload["cycles"]:
        raise AlgorithmException("Real network snapshot thieu cycles.", code=ErrorCode.INVALID_INPUT)
    if not payload["stages"]:
        raise AlgorithmException("Real network snapshot thieu stages.", code=ErrorCode.INVALID_INPUT)
    if payload["sim_to_real"]:
        real_cross_ids = {
            int(cross_id)
            for item in payload["crosses"]
            for cross_id in (
                item.get("id"),
                item.get("cross_id"),
                item.get("crossId"),
                item.get("real_cross_id"),
                item.get("realCrossId"),
            )
            if cross_id is not None
        }
        invalid_mapping: list[dict] = []
        for sim_id, real_id in payload["sim_to_real"].items():
            try:
                normalized_real_id = int(real_id)
            except (TypeError, ValueError):
                invalid_mapping.append({
                    "simId": str(sim_id),
                    "realCrossId": real_id,
                    "reason": "REAL_CROSS_ID_NOT_INTEGER",
                })
                continue
            if normalized_real_id not in real_cross_ids:
                invalid_mapping.append({
                    "simId": str(sim_id),
                    "realCrossId": normalized_real_id,
                    "reason": "REAL_CROSS_NOT_IN_SNAPSHOT",
                })
        if invalid_mapping:
            raise AlgorithmException(
                "simToReal khong hop le: real cross id phai ton tai trong crosses cua snapshot.",
                code=ErrorCode.INVALID_INPUT,
                area_id=area_id,
                extra={"invalidMapping": invalid_mapping},
            )

    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    with get_session() as session:
        dup = _ensure_idempotent(
            session,
            source_event_id=source_event_id,
            event_type="real_network_snapshot.upsert",
            payload=payload,
        )
        if dup:
            return dup

        area_name = str(
            normalized_area.get("area_name")
            or normalized_area.get("AREA_NAME")
            or normalized_area.get("areaName")
            or f"Area {area_id}"
        )
        repo.upsert_area(
            session,
            area_id=area_id,
            area_name=area_name,
            is_active=True,
            controller_visible=True,
            tenant_id=effective_tenant_id,
            network_id=effective_network_id,
        )
        row = repo.upsert_real_network_snapshot(
            session,
            area_id=area_id,
            tenant_id=effective_tenant_id,
            network_id=effective_network_id,
            schema_version=schema_version,
            source_version=source_version,
            payload_json=payload_json,
            checksum=checksum,
        )
        repo.record_sync_event(
            session,
            source_event_id=source_event_id,
            event_type="real_network_snapshot.upsert",
            payload_hash=_hash_payload(payload),
        )

    logger.info(
        f"Sync real_network_snapshot area={area_id} network={effective_network_id} "
        f"crosses={len(crosses)} roads={len(roads)} cycles={len(cycles)} stages={len(stages)}"
    )

    # Eager compile real_normalization.json — pipeline yeu cau snapshot xong la
    # phai co file chuan hoa san sang de controller verify, khong doi den khi co
    # sim bundle moi compile.
    compile_result: Optional[dict] = None
    try:
        from src.ops.real_normalization import compile_real_normalization
        from src.core.config import get_settings
        from pathlib import Path

        settings = get_settings()
        real_norm_dir = Path(settings.model_dir) / "real_normalization" / f"area_{area_id}"
        compile_real_normalization(
            db_url=settings.database_url,
            area_id=area_id,
            output_dir=real_norm_dir,
        )
        clear_intersection_config_cache(area_id)
        compile_result = {"status": "ok", "outputDir": str(real_norm_dir)}
        logger.info(f"[sync] real_normalization eager-compiled -> {real_norm_dir}")
    except Exception as e:
        compile_result = {"status": "failed", "reason": str(e)}
        logger.warning(f"[sync] eager-compile real_normalization fail area={area_id}: {e}")

    # Retry compose cho sim bundle dang cho real snapshot. Idempotent: chi anh
    # huong bundle o status 'pending_real_snapshot'.
    retry_result: Optional[dict] = None
    try:
        from src.ops.lifecycle import retry_pending_sim_bundles

        retry_result = retry_pending_sim_bundles(
            tenant_id=effective_tenant_id,
            network_id=effective_network_id,
            actor="sync-real-network",
        )
        if retry_result["retried"] > 0:
            logger.info(
                f"[sync] retry sim bundles cho network={effective_network_id}: "
                f"{retry_result}"
            )
    except Exception as e:
        retry_result = {"status": "failed", "reason": str(e)}
        logger.warning(f"[sync] retry pending sim bundles fail: {e}")

    return {
        "status": "applied",
        "areaId": area_id,
        "tenantId": effective_tenant_id,
        "networkId": effective_network_id,
        "schemaVersion": schema_version,
        "checksum": row.checksum,
        "counts": {
            "areaCrosses": len(area_crosses),
            "crosses": len(crosses),
            "roads": len(roads),
            "cycles": len(cycles),
            "stages": len(stages),
        },
        "realNormalization": compile_result,
        "retryPendingSimBundles": retry_result,
    }


def get_real_normalization(*, area_id: int) -> dict:
    """Doc real_normalization.json hien tai cho area.

    Tra ve noi dung file da compile boi sync_real_network_snapshot. Neu file
    chua ton tai (snapshot chua duoc upload, hoac compile fail), raise
    AlgorithmException de controller biet ly do.
    """
    from pathlib import Path
    from src.core.config import get_settings

    settings = get_settings()
    real_norm_path = (
        Path(settings.model_dir)
        / "real_normalization"
        / f"area_{area_id}"
        / "real_normalization.json"
    )
    if not real_norm_path.exists():
        raise AlgorithmException(
            f"Area {area_id} chua co real_normalization.json. "
            f"Hay goi PUT /internal/sync/areas/{area_id}/real-network truoc.",
            code=ErrorCode.CONFIG_NOT_FOUND,
            area_id=area_id,
        )
    try:
        payload = json.loads(real_norm_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AlgorithmException(
            f"real_normalization.json hong: {e}",
            code=ErrorCode.INTERNAL_ERROR,
            area_id=area_id,
        ) from e

    return {
        "areaId": area_id,
        "path": str(real_norm_path),
        "content": payload,
    }


def recompile_real_normalization(*, area_id: int) -> dict:
    """Recompile real_normalization tu snapshot da luu trong DB.

    Khong yeu cau snapshot moi — dung lai snapshot hien co cua area. Dung khi
    operator can refresh file chuan hoa sau khi sua data nguon hoac upgrade
    logic chuan hoa.
    """
    from pathlib import Path
    from src.core.config import get_settings
    from src.ops.real_normalization import compile_real_normalization

    settings = get_settings()
    with get_session() as session:
        snapshot = repo.get_real_network_snapshot(session, area_id)
        if snapshot is None:
            raise AlgorithmException(
                f"Area {area_id} chua co real_network_snapshot. "
                f"Hay goi PUT /internal/sync/areas/{area_id}/real-network truoc.",
                code=ErrorCode.CONFIG_NOT_FOUND,
                area_id=area_id,
            )

    real_norm_dir = Path(settings.model_dir) / "real_normalization" / f"area_{area_id}"
    try:
        compile_real_normalization(
            db_url=settings.database_url,
            area_id=area_id,
            output_dir=real_norm_dir,
        )
        clear_intersection_config_cache(area_id)
    except Exception as e:
        raise AlgorithmException(
            f"Compile real_normalization fail: {e}",
            code=ErrorCode.INTERNAL_ERROR,
            area_id=area_id,
        ) from e

    logger.info(f"[sync] recompile real_normalization area={area_id} -> {real_norm_dir}")
    return {
        "status": "recompiled",
        "areaId": area_id,
        "outputDir": str(real_norm_dir),
    }


def finalize_sync(*, area_ids: Optional[list] = None) -> dict:
    """Plan 5.2.4: chot dot dong bo, validate readiness va tra bao cao."""
    from src.services.readiness_service import check_area as _check  # avoid circular at import

    with get_session() as session:
        areas = repo.list_areas(session, only_active=True)
        target_ids = set(area_ids) if area_ids else {a.area_id for a in areas}

    results = []
    for aid in sorted(target_ids):
        check = _check(aid)
        results.append(check.to_dict())

    all_ready = all(r["ready"] for r in results) if results else True
    return {"status": "finalized" if all_ready else "incomplete", "areas": results}
