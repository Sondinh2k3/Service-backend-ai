"""Internal sync API (plan 5.2) — central backend push xuong AI service.

Bao ve bang API key noi bo (header X-Internal-API-Key).
Moi endpoint idempotent theo `sourceEventId`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.core.auth import require_internal_api_key
from src.db import repositories as repo
from src.db.base import get_session
from src.schemas.sync_schemas import (
    AreaArtifactSync,
    AreaUpsert,
    CrossConfigSync,
    FinalizeSync,
    RealNetworkSnapshotSync,
)
from src.services import sync_service
from src.preprocessing.intersection_registry import area_dir

router = APIRouter(
    prefix="/internal/sync",
    dependencies=[Depends(require_internal_api_key)],
    tags=["internal-sync"],
)


@router.put("/areas/{area_id}")
def sync_area(area_id: int, body: AreaUpsert):
    return sync_service.upsert_area(
        area_id=area_id,
        area_name=body.areaName,
        is_active=body.isActive,
        controller_visible=body.controllerVisible,
        tenant_id=body.tenantId,
        network_id=body.networkId,
        source_event_id=body.sourceEventId,
    )


@router.put(
    "/areas/{area_id}/artifacts",
    deprecated=True,
    summary="[DEPRECATED] Push artifact thu cong (legacy path)",
    description=(
        "**DEPRECATED**: Endpoint nay dung cho luong cu (central backend push "
        "tung file policy.onnx + meta + network len service). Pipeline moi "
        "thay the bang Sim Bundle -> Runtime Bundle auto-compose.\n\n"
        "Khuyen nghi: dung `POST /ops/sim-bundles/pull` hoac upload sim bundle "
        "len MinIO de auto-sync listener pickup."
    ),
)
def sync_area_artifact(area_id: int, body: AreaArtifactSync):
    from src.core.logger import logger
    logger.warning(
        f"[deprecated-api] /areas/{area_id}/artifacts duoc goi. "
        f"Pipeline moi nen dung sim-bundle/runtime-bundle workflow."
    )
    # Default path resolution: theo convention <model_dir>/area_<id>/...
    d = area_dir(area_id)
    policy_path = body.policyPath or str(d / "policy.onnx")
    meta_path = body.metaPath or str(d / "policy_meta.json")
    network_path = body.networkPath or str(d / "network.json")
    return sync_service.upsert_artifact(
        area_id=area_id,
        policy_version=body.policyVersion,
        config_version=body.configVersion,
        policy_path=policy_path,
        meta_path=meta_path,
        network_path=network_path,
        checksum=body.checksum,
        activate=body.activate,
        source_event_id=body.sourceEventId,
    )


@router.post(
    "/areas/{area_id}/artifacts/{artifact_id}/activate",
    deprecated=True,
    summary="[DEPRECATED] Activate artifact legacy",
    description=(
        "**DEPRECATED**: Dung cho artifact path cu. Pipeline moi dung "
        "`POST /ops/bundles/{bundle_id}/activate` (model bundle) thay vi."
    ),
)
def activate_artifact(area_id: int, artifact_id: int):
    """[DEPRECATED] Kich hoat artifact (plan 11.3.4: version activate workflow)."""
    from src.core.logger import logger
    from src.services.model_manager import clear_cache

    logger.warning(
        f"[deprecated-api] /areas/{area_id}/artifacts/{artifact_id}/activate duoc goi."
    )
    with get_session() as session:
        art = repo.activate_artifact(session, artifact_id)
    clear_cache(area_id)
    return {
        "status": "activated",
        "areaId": area_id,
        "artifactId": art.id,
        "policyVersion": art.policy_version,
        "configVersion": art.config_version,
    }


@router.put("/areas/{area_id}/crosses/{cross_id}/config")
def sync_cross_config(area_id: int, cross_id: int, body: CrossConfigSync):
    return sync_service.sync_cross_config(
        area_id=area_id,
        cross_id=cross_id,
        config_payload=body.to_config_payload(cross_id),
        config_version=body.configVersion,
        source_event_id=body.sourceEventId,
    )


@router.put("/areas/{area_id}/real-network")
def sync_real_network_snapshot(area_id: int, body: RealNetworkSnapshotSync):
    return sync_service.sync_real_network_snapshot(
        area_id=area_id,
        tenant_id=body.tenantId,
        network_id=body.networkId,
        schema_version=body.schemaVersion,
        source_version=body.sourceVersion,
        area=body.area,
        area_crosses=body.areaCrosses,
        crosses=body.crosses,
        roads=body.roads,
        cycles=body.cycles,
        stages=body.stages,
        sim_to_real=body.simToReal,
        source_event_id=body.sourceEventId,
    )


@router.get(
    "/areas/{area_id}/real-normalization",
    summary="Xem real_normalization.json da compile cho area",
    description=(
        "Tra ve noi dung real_normalization.json hien tai cua area. "
        "File nay duoc service tu compile sau khi nhan real_network_snapshot. "
        "Controller co the goi endpoint nay de verify chuan hoa truoc khi "
        "training team upload sim bundle."
    ),
)
def get_real_normalization(area_id: int):
    return sync_service.get_real_normalization(area_id=area_id)


@router.post(
    "/areas/{area_id}/real-normalization/recompile",
    summary="Recompile real_normalization tu snapshot da luu",
    description=(
        "Goi tay luong chuan hoa khi snapshot DB co thay doi ngoai luong sync "
        "thong thuong (vd: fix data manual). Idempotent — co the goi nhieu lan."
    ),
)
def recompile_real_normalization(area_id: int):
    return sync_service.recompile_real_normalization(area_id=area_id)


@router.post("/finalize")
def finalize(body: FinalizeSync):
    return sync_service.finalize_sync(area_ids=body.areaIds)
