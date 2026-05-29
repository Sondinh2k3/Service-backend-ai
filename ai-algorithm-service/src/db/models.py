"""SQLAlchemy models — theo schema plan muc 4.2 + RLOps Lop 2.

Bang:
  - area_registry            (legacy, area_id == network_id mapping cho MVP)
  - area_artifact            (legacy, giu cho backward-compat)
  - area_cross_config        (legacy)
  - real_network_snapshot    (service-owned snapshot tu central/backend UI)
  - sync_event               (idempotency)
  - inference_audit
  - model_bundle             (NEW) — Model Bundle phia Edge, drives ai-ops/ai-runtime
  - bundle_event             (NEW) — audit cho moi thao tac quan tri bundle
  - drift_event              (NEW) — log drift detection
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class AreaRegistry(Base):
    __tablename__ = "area_registry"

    area_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    area_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Mapping ra dinh danh RLOps. Cho MVP: tenant_id mac dinh "default",
    # network_id == f"area_{area_id}" neu khong set.
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    network_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    controller_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    artifacts: Mapped[list["AreaArtifact"]] = relationship(
        back_populates="area", cascade="all, delete-orphan"
    )
    cross_configs: Mapped[list["AreaCrossConfig"]] = relationship(
        back_populates="area", cascade="all, delete-orphan"
    )
    real_network_snapshot: Mapped[Optional["RealNetworkSnapshot"]] = relationship(
        back_populates="area", cascade="all, delete-orphan"
    )
    bundles: Mapped[list["ModelBundle"]] = relationship(
        back_populates="area", cascade="all, delete-orphan"
    )


class AreaArtifact(Base):
    """Metadata cho bo artifact policy + config cua area.

    Luu version + path; file model van duoc quan ly thu cong (plan muc 5.2.2).
    """

    __tablename__ = "area_artifact"
    __table_args__ = (
        UniqueConstraint("area_id", "policy_version", "config_version", name="uq_artifact_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("area_registry.area_id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_path: Mapped[str] = mapped_column(String(512), nullable=False)
    meta_path: Mapped[str] = mapped_column(String(512), nullable=False)
    network_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Trang thai: ready | invalid | deprecated
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="invalid")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    area: Mapped[AreaRegistry] = relationship(back_populates="artifacts")


class AreaCrossConfig(Base):
    """Config cross-level (direction_map, phase_mapping, observation_mask)."""

    __tablename__ = "area_cross_config"
    __table_args__ = (
        UniqueConstraint("area_id", "cross_id", name="uq_area_cross"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("area_registry.area_id", ondelete="CASCADE"), nullable=False, index=True
    )
    cross_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    config_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    area: Mapped[AreaRegistry] = relationship(back_populates="cross_configs")


class RealNetworkSnapshot(Base):
    """Snapshot mang luoi thuc do backend/controller push vao AI service.

    Day la DB noi bo cua service cho flow sim-to-real moi. Payload giu cac bang
    tuong duong `management.sql`: area, area_crosses, crosses, roads, cycles,
    stages va optional sim_to_real mapping.
    """

    __tablename__ = "real_network_snapshot"

    area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("area_registry.area_id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    network_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="real-network/v1")
    source_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    area: Mapped[AreaRegistry] = relationship(back_populates="real_network_snapshot")


class SyncEvent(Base):
    """Ban ghi idempotency cho moi request dong bo tu central backend."""

    __tablename__ = "sync_event"
    __table_args__ = (
        UniqueConstraint("source_event_id", name="uq_source_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, default="central-backend")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="applied")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class InferenceAudit(Base):
    """Log moi inference request de audit / debug SLA."""

    __tablename__ = "inference_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    area_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    bundle_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    policy_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    config_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    num_crosses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    guardrail_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ModelBundle(Base):
    """Model Bundle metadata tren Edge.

    Status: pulled | validated | active | rejected | rolled_back | deprecated
    """

    __tablename__ = "model_bundle"
    __table_args__ = (
        UniqueConstraint("bundle_id", name="uq_bundle_id"),
        UniqueConstraint(
            "tenant_id",
            "network_id",
            "version",
            "bundle_kind",
            name="uq_bundle_tenant_network_version_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    bundle_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="runtime")
    parent_bundle_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    network_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    area_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("area_registry.area_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    topology_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    source_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pulled")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    training_run_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    training_dataset_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    training_pipeline_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    manifest_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    area: Mapped[Optional[AreaRegistry]] = relationship(back_populates="bundles")


class BundleEvent(Base):
    """Audit moi thao tac quan tri bundle (pull/validate/activate/rollback/reject)."""

    __tablename__ = "bundle_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="ai-ops")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DriftEvent(Base):
    """Drift detection events (Lop 4 — Observability)."""

    __tablename__ = "drift_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    network_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    bundle_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    feature: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    threshold: Mapped[float] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warn")
    window_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
