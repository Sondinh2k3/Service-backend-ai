"""ai-ops REST API.

Endpoints:
  GET   /ops/bundles                            — list bundles
  GET   /ops/bundles/{bundle_id}                — chi tiet
  POST  /ops/bundles/pull                       — pull tu source_uri
  POST  /ops/bundles/register-local             — register tu Local Storage
  POST  /ops/bundles/{bundle_id}/activate       — kich hoat
  POST  /ops/networks/{network_id}/rollback     — rollback ve bundle truoc
  GET   /ops/networks/{network_id}/active       — xem ActivePointer
  GET   /ops/bundles/{bundle_id}/events         — lich su event

Bao ve bang `INTERNAL_API_KEY` (cung header voi sync API).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from src.bundles import network_dir, read_active_pointer
from src.core.auth import require_internal_api_key
from src.core.config import get_settings
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.db import repositories as repo
from src.db.base import get_session
from src.ops import lifecycle


router = APIRouter(
    prefix="/ops",
    dependencies=[Depends(require_internal_api_key)],
    tags=["ai-ops"],
)


class PullBundleRequest(BaseModel):
    sourceUri: str = Field(..., description="s3:// hoac minio:// URI cua bundle.zip")
    activate: bool = Field(default=False, description="Activate ngay sau khi pull thanh cong")


class PullSimBundleRequest(BaseModel):
    sourceUri: str = Field(..., description="s3:// hoac minio:// URI cua sim bundle.zip")
    activate: bool = Field(default=False, description="Activate runtime bundle sau khi compose")


class RegisterLocalRequest(BaseModel):
    bundleDir: str = Field(..., description="Duong dan thu muc bundle da giai nen")
    activate: bool = Field(default=False)


class RollbackRequest(BaseModel):
    tenantId: Optional[str] = Field(default=None)


def _bundle_to_dict(bundle: Any) -> dict:
    return {
        "bundleId": bundle.bundle_id,
        "bundleKind": getattr(bundle, "bundle_kind", "runtime"),
        "parentBundleId": getattr(bundle, "parent_bundle_id", None),
        "tenantId": bundle.tenant_id,
        "networkId": bundle.network_id,
        "areaId": bundle.area_id,
        "version": bundle.version,
        "configVersion": bundle.config_version,
        "topologyHash": bundle.topology_hash,
        "checksum": bundle.checksum,
        "status": bundle.status,
        "isActive": bundle.is_active,
        "sourceUri": bundle.source_uri,
        "localPath": bundle.local_path,
        "trainingRunId": bundle.training_run_id,
        "trainingDatasetId": bundle.training_dataset_id,
        "trainingPipelineCommit": bundle.training_pipeline_commit,
        "activatedAt": bundle.activated_at.isoformat() if bundle.activated_at else None,
        "deactivatedAt": bundle.deactivated_at.isoformat() if bundle.deactivated_at else None,
        "rejectedReason": bundle.rejected_reason,
        "createdAt": bundle.created_at.isoformat() if bundle.created_at else None,
    }


@router.get("/bundles")
def list_bundles(
    tenant_id: Optional[str] = Query(default=None, alias="tenantId"),
    network_id: Optional[str] = Query(default=None, alias="networkId"),
    status: Optional[str] = Query(default=None),
    bundle_kind: Optional[str] = Query(default=None, alias="bundleKind"),
):
    with get_session() as s:
        bundles = repo.list_bundles(
            s,
            tenant_id=tenant_id,
            network_id=network_id,
            status=status,
            bundle_kind=bundle_kind,
        )
        items = [_bundle_to_dict(b) for b in bundles]
    return {"bundles": items}


@router.get("/bundles/{bundle_id}")
def get_bundle(bundle_id: str):
    with get_session() as s:
        bundle = repo.get_bundle(s, bundle_id)
        if bundle is None:
            raise AlgorithmException(
                f"Bundle khong ton tai: {bundle_id}",
                code=ErrorCode.AREA_NOT_FOUND,
            )
        manifest_obj = None
        if bundle.manifest_json:
            try:
                manifest_obj = json.loads(bundle.manifest_json)
            except json.JSONDecodeError:
                manifest_obj = None
        payload = _bundle_to_dict(bundle)
        payload["manifest"] = manifest_obj
    return payload


@router.post("/bundles/pull")
def pull_bundle(body: PullBundleRequest, request: Request):
    request_id = getattr(request.state, "request_id", "")
    try:
        bundle = lifecycle.pull_and_register_bundle(
            source_uri=body.sourceUri,
            request_id=request_id,
        )
    except lifecycle.BundleLifecycleError as e:
        raise AlgorithmException(
            str(e), code=ErrorCode.INVALID_INPUT
        ) from e

    response = {"status": "validated", "bundle": _bundle_to_dict(bundle)}
    if body.activate:
        bundle = lifecycle.activate_bundle(bundle.bundle_id, request_id=request_id)
        response["status"] = "activated"
        response["bundle"] = _bundle_to_dict(bundle)
    return response


@router.post("/sim-bundles/pull")
def pull_sim_bundle(body: PullSimBundleRequest, request: Request):
    request_id = getattr(request.state, "request_id", "")
    try:
        bundle = lifecycle.pull_and_register_bundle_auto(
            source_uri=body.sourceUri,
            request_id=request_id,
            auto_activate=body.activate,
        )
    except lifecycle.BundleLifecycleError as e:
        raise AlgorithmException(
            str(e), code=ErrorCode.INVALID_INPUT
        ) from e

    response = {"status": "validated", "bundle": _bundle_to_dict(bundle)}
    if body.activate:
        bundle = lifecycle.activate_bundle(bundle.bundle_id, request_id=request_id)
        response["status"] = "activated"
        response["bundle"] = _bundle_to_dict(bundle)
    return response


@router.post("/bundles/register-local")
def register_local(body: RegisterLocalRequest, request: Request):
    request_id = getattr(request.state, "request_id", "")
    try:
        bundle = lifecycle.register_local_bundle(
            bundle_dir=Path(body.bundleDir),
            request_id=request_id,
        )
    except lifecycle.BundleLifecycleError as e:
        raise AlgorithmException(
            str(e), code=ErrorCode.INVALID_INPUT
        ) from e

    response = {"status": "validated", "bundle": _bundle_to_dict(bundle)}
    if body.activate:
        bundle = lifecycle.activate_bundle(bundle.bundle_id, request_id=request_id)
        response["status"] = "activated"
        response["bundle"] = _bundle_to_dict(bundle)
    return response


@router.post("/bundles/{bundle_id}/activate")
def activate(bundle_id: str, request: Request):
    request_id = getattr(request.state, "request_id", "")
    try:
        bundle = lifecycle.activate_bundle(bundle_id, request_id=request_id)
    except lifecycle.BundleLifecycleError as e:
        raise AlgorithmException(
            str(e), code=ErrorCode.INVALID_INPUT
        ) from e
    return {"status": "activated", "bundle": _bundle_to_dict(bundle)}


@router.post("/networks/{network_id}/rollback")
def rollback(network_id: str, body: RollbackRequest, request: Request):
    request_id = getattr(request.state, "request_id", "")
    try:
        bundle = lifecycle.rollback_bundle(
            network_id, tenant_id=body.tenantId, request_id=request_id
        )
    except lifecycle.BundleLifecycleError as e:
        raise AlgorithmException(
            str(e), code=ErrorCode.INVALID_INPUT
        ) from e
    return {
        "status": "rolled_back",
        "activeBundle": _bundle_to_dict(bundle) if bundle else None,
    }


@router.get("/networks/{network_id}/active")
def get_active(network_id: str):
    pointer = read_active_pointer(network_dir(network_id))
    if pointer is None:
        raise AlgorithmException(
            f"Network {network_id} chua co active bundle.",
            code=ErrorCode.AREA_NOT_READY,
        )
    return pointer.to_dict()


@router.get("/auto-sync/status")
def auto_sync_status():
    """Trang thai auto-sync (listener + safety-net poller)."""
    from src.ops import auto_sync
    return auto_sync.status()


@router.post("/auto-sync/scan-now")
def auto_sync_scan_now():
    """Trigger 1 lan scan MinIO bucket ngay (khong doi den interval poller)."""
    from src.ops import auto_sync
    from src.services import artifact_storage
    settings = get_settings()
    prefix = settings.sim_bundle_prefix or settings.minio_auto_sync_prefix
    suffix = settings.sim_bundle_suffix if settings.sim_bundle_auto_compose_enabled else settings.minio_auto_sync_suffix
    uris = artifact_storage.list_remote_zips(
        prefix=prefix,
        suffix=suffix,
    )
    pulled = []
    for uri in uris:
        before = auto_sync.status()["pulled_count"]
        auto_sync._handle_uri(uri, actor="manual-scan")
        after = auto_sync.status()["pulled_count"]
        if after > before:
            pulled.append(uri)
    return {"scanned": len(uris), "pulled": pulled}


@router.get("/bundles/{bundle_id}/events")
def list_events(bundle_id: str, limit: int = Query(default=100, ge=1, le=1000)):
    with get_session() as s:
        events = repo.list_bundle_events(s, bundle_id, limit=limit)
        items = [
            {
                "eventType": ev.event_type,
                "status": ev.status,
                "actor": ev.actor,
                "detail": ev.detail,
                "requestId": ev.request_id,
                "createdAt": ev.created_at.isoformat() if ev.created_at else None,
            }
            for ev in events
        ]
    return {"bundleId": bundle_id, "events": items}
