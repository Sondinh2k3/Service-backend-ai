"""Repository helpers cho DB layer.

Khong dung ORM truc tiep tu service/api — tat ca DB access di qua day de:
  - Kiem tra schema thay doi 1 cho
  - De mock / thay the (vd chuyen sang async sau nay)
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    AreaArtifact,
    AreaCrossConfig,
    AreaRegistry,
    BundleEvent,
    DriftEvent,
    InferenceAudit,
    ModelBundle,
    RealNetworkSnapshot,
    SyncEvent,
)


# ----- area_registry

def upsert_area(
    session: Session,
    *,
    area_id: int,
    area_name: str = "",
    is_active: bool = True,
    controller_visible: bool = True,
    tenant_id: Optional[str] = None,
    network_id: Optional[str] = None,
) -> AreaRegistry:
    area = session.get(AreaRegistry, area_id)
    default_network_id = network_id or f"area_{area_id}"
    default_tenant_id = tenant_id or "default"
    if area is None:
        area = AreaRegistry(
            area_id=area_id,
            area_name=area_name,
            is_active=is_active,
            controller_visible=controller_visible,
            tenant_id=default_tenant_id,
            network_id=default_network_id,
        )
        session.add(area)
    else:
        if area_name:
            area.area_name = area_name
        area.is_active = is_active
        area.controller_visible = controller_visible
        if tenant_id is not None:
            area.tenant_id = tenant_id
        if network_id is not None:
            area.network_id = network_id
        elif not area.network_id:
            area.network_id = default_network_id
        if not area.tenant_id:
            area.tenant_id = default_tenant_id
    session.flush()
    return area


def get_area_by_network(
    session: Session, tenant_id: str, network_id: str
) -> Optional[AreaRegistry]:
    stmt = select(AreaRegistry).where(
        AreaRegistry.tenant_id == tenant_id,
        AreaRegistry.network_id == network_id,
    )
    return session.scalars(stmt).first()


def get_area(session: Session, area_id: int) -> Optional[AreaRegistry]:
    return session.get(AreaRegistry, area_id)


def list_areas(
    session: Session,
    *,
    only_active: bool = False,
    only_visible: bool = False,
) -> List[AreaRegistry]:
    stmt = select(AreaRegistry)
    if only_active:
        stmt = stmt.where(AreaRegistry.is_active.is_(True))
    if only_visible:
        stmt = stmt.where(AreaRegistry.controller_visible.is_(True))
    stmt = stmt.order_by(AreaRegistry.area_id)
    return list(session.scalars(stmt))


# ----- area_artifact

def upsert_artifact(
    session: Session,
    *,
    area_id: int,
    policy_version: str,
    config_version: str,
    policy_path: str,
    meta_path: str,
    network_path: Optional[str] = None,
    checksum: Optional[str] = None,
    status: str = "invalid",
) -> AreaArtifact:
    stmt = select(AreaArtifact).where(
        AreaArtifact.area_id == area_id,
        AreaArtifact.policy_version == policy_version,
        AreaArtifact.config_version == config_version,
    )
    art = session.scalars(stmt).first()
    if art is None:
        art = AreaArtifact(
            area_id=area_id,
            policy_version=policy_version,
            config_version=config_version,
            policy_path=policy_path,
            meta_path=meta_path,
            network_path=network_path,
            checksum=checksum,
            status=status,
        )
        session.add(art)
    else:
        art.policy_path = policy_path
        art.meta_path = meta_path
        art.network_path = network_path
        art.checksum = checksum
        art.status = status
    session.flush()
    return art


def get_active_artifact(session: Session, area_id: int) -> Optional[AreaArtifact]:
    stmt = (
        select(AreaArtifact)
        .where(AreaArtifact.area_id == area_id, AreaArtifact.is_active.is_(True))
        .order_by(AreaArtifact.activated_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def list_artifacts(session: Session, area_id: int) -> List[AreaArtifact]:
    stmt = (
        select(AreaArtifact)
        .where(AreaArtifact.area_id == area_id)
        .order_by(AreaArtifact.created_at.desc())
    )
    return list(session.scalars(stmt))


def activate_artifact(session: Session, artifact_id: int) -> AreaArtifact:
    """Set artifact active, deactivate cac artifact khac cua cung area."""
    art = session.get(AreaArtifact, artifact_id)
    if art is None:
        raise ValueError(f"Artifact id={artifact_id} not found")
    # Deactivate sibling
    siblings = session.scalars(
        select(AreaArtifact).where(
            AreaArtifact.area_id == art.area_id, AreaArtifact.id != art.id
        )
    ).all()
    for s in siblings:
        if s.is_active:
            s.is_active = False
            s.status = "deprecated"
    art.is_active = True
    art.status = "ready"
    art.activated_at = datetime.utcnow()
    session.flush()
    return art


def set_artifact_status(session: Session, artifact_id: int, status: str) -> None:
    art = session.get(AreaArtifact, artifact_id)
    if art is None:
        return
    art.status = status
    session.flush()


# ----- area_cross_config

def upsert_cross_config(
    session: Session,
    *,
    area_id: int,
    cross_id: int,
    payload_json: str,
    config_version: str = "1",
    checksum: Optional[str] = None,
) -> AreaCrossConfig:
    stmt = select(AreaCrossConfig).where(
        AreaCrossConfig.area_id == area_id, AreaCrossConfig.cross_id == cross_id
    )
    row = session.scalars(stmt).first()
    if row is None:
        row = AreaCrossConfig(
            area_id=area_id,
            cross_id=cross_id,
            config_payload_json=payload_json,
            config_version=config_version,
            checksum=checksum,
        )
        session.add(row)
    else:
        row.config_payload_json = payload_json
        row.config_version = config_version
        row.checksum = checksum
    session.flush()
    return row


def list_cross_configs(session: Session, area_id: int) -> List[AreaCrossConfig]:
    stmt = select(AreaCrossConfig).where(AreaCrossConfig.area_id == area_id)
    return list(session.scalars(stmt))


# ----- real_network_snapshot

def upsert_real_network_snapshot(
    session: Session,
    *,
    area_id: int,
    tenant_id: str,
    network_id: str,
    schema_version: str,
    payload_json: str,
    checksum: str,
    source_version: Optional[str] = None,
) -> RealNetworkSnapshot:
    row = session.get(RealNetworkSnapshot, area_id)
    if row is None:
        row = RealNetworkSnapshot(
            area_id=area_id,
            tenant_id=tenant_id,
            network_id=network_id,
            schema_version=schema_version,
            source_version=source_version,
            payload_json=payload_json,
            checksum=checksum,
        )
        session.add(row)
    else:
        row.tenant_id = tenant_id
        row.network_id = network_id
        row.schema_version = schema_version
        row.source_version = source_version
        row.payload_json = payload_json
        row.checksum = checksum
    session.flush()
    return row


def get_real_network_snapshot(session: Session, area_id: int) -> Optional[RealNetworkSnapshot]:
    return session.get(RealNetworkSnapshot, area_id)


# ----- sync_event (idempotency)

def get_sync_event(session: Session, source_event_id: str) -> Optional[SyncEvent]:
    stmt = select(SyncEvent).where(SyncEvent.source_event_id == source_event_id)
    return session.scalars(stmt).first()


def record_sync_event(
    session: Session,
    *,
    source_event_id: str,
    event_type: str,
    payload_hash: str,
    source_system: str = "central-backend",
    status: str = "applied",
    error_message: Optional[str] = None,
) -> SyncEvent:
    existing = get_sync_event(session, source_event_id)
    if existing is not None:
        return existing
    ev = SyncEvent(
        source_event_id=source_event_id,
        source_system=source_system,
        event_type=event_type,
        payload_hash=payload_hash,
        status=status,
        error_message=error_message,
    )
    session.add(ev)
    session.flush()
    return ev


# ----- inference_audit

def record_inference_audit(
    session: Session,
    *,
    request_id: str,
    area_id: Optional[int],
    policy_version: Optional[str],
    config_version: Optional[str],
    num_crosses: int,
    latency_ms: int,
    status: str,
    error_code: Optional[str] = None,
    bundle_id: Optional[str] = None,
    guardrail_triggered: bool = False,
) -> InferenceAudit:
    row = InferenceAudit(
        request_id=request_id,
        area_id=area_id,
        bundle_id=bundle_id,
        policy_version=policy_version,
        config_version=config_version,
        num_crosses=num_crosses,
        latency_ms=latency_ms,
        status=status,
        error_code=error_code,
        guardrail_triggered=guardrail_triggered,
    )
    session.add(row)
    session.flush()
    return row


# ----- model_bundle (Lop 2 — RLOps)

def upsert_bundle(
    session: Session,
    *,
    bundle_id: str,
    bundle_kind: str = "runtime",
    parent_bundle_id: Optional[str] = None,
    tenant_id: str,
    network_id: str,
    version: str,
    config_version: str,
    topology_hash: str,
    checksum: str,
    area_id: Optional[int] = None,
    source_uri: Optional[str] = None,
    local_path: Optional[str] = None,
    status: str = "pulled",
    training_run_id: Optional[str] = None,
    training_dataset_id: Optional[str] = None,
    training_pipeline_commit: Optional[str] = None,
    manifest_json: Optional[str] = None,
) -> ModelBundle:
    stmt = select(ModelBundle).where(ModelBundle.bundle_id == bundle_id)
    bundle = session.scalars(stmt).first()
    if bundle is None:
        bundle = ModelBundle(
            bundle_id=bundle_id,
            bundle_kind=bundle_kind,
            parent_bundle_id=parent_bundle_id,
            tenant_id=tenant_id,
            network_id=network_id,
            area_id=area_id,
            version=version,
            config_version=config_version,
            topology_hash=topology_hash,
            checksum=checksum,
            source_uri=source_uri,
            local_path=local_path,
            status=status,
            training_run_id=training_run_id,
            training_dataset_id=training_dataset_id,
            training_pipeline_commit=training_pipeline_commit,
            manifest_json=manifest_json,
        )
        session.add(bundle)
    else:
        bundle.bundle_kind = bundle_kind
        bundle.parent_bundle_id = parent_bundle_id
        bundle.tenant_id = tenant_id
        bundle.network_id = network_id
        if area_id is not None:
            bundle.area_id = area_id
        bundle.version = version
        bundle.config_version = config_version
        bundle.topology_hash = topology_hash
        bundle.checksum = checksum
        if source_uri is not None:
            bundle.source_uri = source_uri
        if local_path is not None:
            bundle.local_path = local_path
        bundle.status = status
        if training_run_id is not None:
            bundle.training_run_id = training_run_id
        if training_dataset_id is not None:
            bundle.training_dataset_id = training_dataset_id
        if training_pipeline_commit is not None:
            bundle.training_pipeline_commit = training_pipeline_commit
        if manifest_json is not None:
            bundle.manifest_json = manifest_json
    session.flush()
    return bundle


def get_bundle(session: Session, bundle_id: str) -> Optional[ModelBundle]:
    stmt = select(ModelBundle).where(ModelBundle.bundle_id == bundle_id)
    return session.scalars(stmt).first()


def bundle_exists_by_source_uri(session: Session, source_uri: str) -> bool:
    """Check da co bundle pull tu source_uri nay chua. Dung cho auto-sync dedup."""
    stmt = select(ModelBundle.bundle_id).where(ModelBundle.source_uri == source_uri).limit(1)
    return session.scalars(stmt).first() is not None


def list_bundles(
    session: Session,
    *,
    tenant_id: Optional[str] = None,
    network_id: Optional[str] = None,
    area_id: Optional[int] = None,
    status: Optional[str] = None,
    bundle_kind: Optional[str] = None,
) -> List[ModelBundle]:
    stmt = select(ModelBundle)
    if tenant_id is not None:
        stmt = stmt.where(ModelBundle.tenant_id == tenant_id)
    if network_id is not None:
        stmt = stmt.where(ModelBundle.network_id == network_id)
    if area_id is not None:
        stmt = stmt.where(ModelBundle.area_id == area_id)
    if status is not None:
        stmt = stmt.where(ModelBundle.status == status)
    if bundle_kind is not None:
        stmt = stmt.where(ModelBundle.bundle_kind == bundle_kind)
    stmt = stmt.order_by(ModelBundle.created_at.desc())
    return list(session.scalars(stmt))


def get_active_bundle(
    session: Session,
    *,
    tenant_id: Optional[str] = None,
    network_id: Optional[str] = None,
    area_id: Optional[int] = None,
) -> Optional[ModelBundle]:
    stmt = select(ModelBundle).where(ModelBundle.is_active.is_(True))
    if tenant_id is not None:
        stmt = stmt.where(ModelBundle.tenant_id == tenant_id)
    if network_id is not None:
        stmt = stmt.where(ModelBundle.network_id == network_id)
    if area_id is not None:
        stmt = stmt.where(ModelBundle.area_id == area_id)
    stmt = stmt.order_by(ModelBundle.activated_at.desc()).limit(1)
    return session.scalars(stmt).first()


def get_previous_active_bundle(
    session: Session,
    *,
    tenant_id: str,
    network_id: str,
    exclude_bundle_id: str,
) -> Optional[ModelBundle]:
    """Bundle gan nhat tung active (de rollback)."""
    stmt = (
        select(ModelBundle)
        .where(
            ModelBundle.tenant_id == tenant_id,
            ModelBundle.network_id == network_id,
            ModelBundle.bundle_id != exclude_bundle_id,
            ModelBundle.activated_at.is_not(None),
        )
        .order_by(ModelBundle.activated_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def activate_bundle(session: Session, bundle_id: str) -> ModelBundle:
    """Mark bundle Active, deactivate cac bundle khac cua cung (tenant, network)."""
    bundle = get_bundle(session, bundle_id)
    if bundle is None:
        raise ValueError(f"Bundle {bundle_id} not found")
    siblings = session.scalars(
        select(ModelBundle).where(
            ModelBundle.tenant_id == bundle.tenant_id,
            ModelBundle.network_id == bundle.network_id,
            ModelBundle.bundle_id != bundle.bundle_id,
        )
    ).all()
    now = datetime.utcnow()
    for s in siblings:
        if s.is_active:
            s.is_active = False
            s.deactivated_at = now
            if s.status == "active":
                s.status = "deprecated"
    bundle.is_active = True
    bundle.status = "active"
    bundle.activated_at = now
    bundle.deactivated_at = None
    bundle.rejected_reason = None
    session.flush()
    return bundle


def set_bundle_status(
    session: Session,
    bundle_id: str,
    status: str,
    *,
    rejected_reason: Optional[str] = None,
) -> Optional[ModelBundle]:
    bundle = get_bundle(session, bundle_id)
    if bundle is None:
        return None
    bundle.status = status
    if rejected_reason is not None:
        bundle.rejected_reason = rejected_reason
    if status in ("rejected", "rolled_back", "deprecated"):
        bundle.is_active = False
        if bundle.activated_at and bundle.deactivated_at is None:
            bundle.deactivated_at = datetime.utcnow()
    session.flush()
    return bundle


def record_bundle_event(
    session: Session,
    *,
    bundle_id: str,
    event_type: str,
    status: str = "ok",
    detail: Optional[str] = None,
    request_id: Optional[str] = None,
    actor: str = "ai-ops",
) -> BundleEvent:
    row = BundleEvent(
        bundle_id=bundle_id,
        event_type=event_type,
        status=status,
        detail=detail,
        request_id=request_id,
        actor=actor,
    )
    session.add(row)
    session.flush()
    return row


def list_bundle_events(
    session: Session,
    bundle_id: str,
    *,
    limit: int = 100,
) -> List[BundleEvent]:
    stmt = (
        select(BundleEvent)
        .where(BundleEvent.bundle_id == bundle_id)
        .order_by(BundleEvent.created_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


# ----- drift_event

def record_drift_event(
    session: Session,
    *,
    network_id: str,
    feature: str,
    method: str,
    score: float,
    threshold: float,
    severity: str = "warn",
    bundle_id: Optional[str] = None,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    detail: Optional[str] = None,
) -> DriftEvent:
    row = DriftEvent(
        network_id=network_id,
        bundle_id=bundle_id,
        feature=feature,
        method=method,
        score=score,
        threshold=threshold,
        severity=severity,
        window_start=window_start,
        window_end=window_end,
        detail=detail,
    )
    session.add(row)
    session.flush()
    return row
