"""Bundle lifecycle: pull -> validate -> activate -> rollback.

Day la nhan vat chinh cua ai-ops. Moi thao tac:
  1. Tao ban ghi BundleEvent truoc khi action.
  2. Doi trang thai ModelBundle.
  3. Cap nhat ActivePointer (file).
  4. Goi ai-runtime hot-reload (qua HTTP) — best effort.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.bundles import (
    ActivePointer,
    BundleValidationError,
    ModelManifest,
    bundle_root,
    bundle_zip_path,
    extract_bundle_zip,
    network_dir,
    remote_bundle_uri,
    sim_bundle_root,
    sim_bundle_zip_path,
    validate_bundle_dir,
    write_active_pointer,
)
from src.core.config import get_settings
from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session
from src.db.models import AreaRegistry, ModelBundle
from src.services import artifact_storage
from src.ops.composer import (
    ComposeError,
    MissingRealSnapshotError,
    compose_runtime_bundle_from_sim_zip,
)
from src.ops.sim_bundle import (
    SimBundleValidationError,
    extract_sim_bundle_zip,
    is_sim_bundle_zip,
    validate_sim_bundle_dir,
)


class BundleLifecycleError(Exception):
    """Loi nghiep vu khi quan ly bundle. Rollback khong duoc thuc thi."""


# ----------------------------------------------------------------------
# Pull
# ----------------------------------------------------------------------

def pull_and_register_bundle(
    *,
    source_uri: str,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
    auto_activate: bool = False,
) -> ModelBundle:
    """Tai bundle ZIP tu `source_uri` (s3://...), giai nen, validate, ghi DB.

    Khi `auto_activate=True`, kich hoat ngay sau khi pull thanh cong (best-effort
    — neu activate fail, bundle van o status='validated' va caller co the
    activate manual sau).

    Caller goi `activate_bundle()` rieng sau khi auto_activate=False.
    """
    settings = get_settings()
    if not settings.minio_enabled:
        raise BundleLifecycleError(
            "MinIO khong duoc bat — ai-ops khong the pull bundle."
        )

    tmp_zip_path = _download_to_tmp_zip(source_uri)
    try:
        bundle = _register_bundle_from_zip(
            zip_path=tmp_zip_path,
            source_uri=source_uri,
            actor=actor,
            request_id=request_id,
            auto_activate=auto_activate,
        )
    finally:
        _cleanup_path(tmp_zip_path)
    return bundle


def pull_and_register_bundle_auto(
    *,
    source_uri: str,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
    auto_activate: bool = False,
) -> ModelBundle:
    """Auto-detect sim bundle vs runtime bundle.

    - Sim bundle: compose -> runtime bundle -> register
    - Runtime bundle: validate + register (normal)
    """
    tmp_zip_path = _download_to_tmp_zip(source_uri)
    try:
        if is_sim_bundle_zip(tmp_zip_path):
            return pull_and_register_sim_bundle(
                sim_source_uri=source_uri,
                sim_zip_path=tmp_zip_path,
                actor=actor,
                request_id=request_id,
                auto_activate=auto_activate,
            )
        return _register_bundle_from_zip(
            zip_path=tmp_zip_path,
            source_uri=source_uri,
            actor=actor,
            request_id=request_id,
            auto_activate=auto_activate,
        )
    finally:
        _cleanup_path(tmp_zip_path)


def pull_and_register_sim_bundle(
    *,
    sim_source_uri: str,
    sim_zip_path: Path,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
    auto_activate: bool = False,
) -> ModelBundle:
    """Handle sim bundle: validate + store + compose runtime bundle."""
    settings = get_settings()
    staging_root = sim_zip_path.parent / f"sim_extract_{sim_zip_path.stem}"
    staging_root.mkdir(parents=True, exist_ok=True)

    try:
        extracted = extract_sim_bundle_zip(sim_zip_path, staging_root)
        sim_manifest = validate_sim_bundle_dir(extracted)
    except SimBundleValidationError as e:
        _cleanup_path(staging_root)
        raise BundleLifecycleError(f"Sim bundle fail validate: {e}") from e

    # Store sim bundle locally for trace/audit
    sim_target_root = sim_bundle_root(sim_manifest.sim_bundle_id)
    if sim_target_root.exists():
        shutil.rmtree(sim_target_root)
    sim_target_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extracted), str(sim_target_root))

    sim_zip_target = sim_bundle_zip_path(sim_manifest.sim_bundle_id)
    sim_zip_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sim_zip_path, sim_zip_target)
    _cleanup_path(staging_root)

    # Register sim bundle metadata into DB
    with get_session() as s:
        repo.upsert_bundle(
            s,
            bundle_id=sim_manifest.sim_bundle_id,
            bundle_kind="sim",
            parent_bundle_id=None,
            tenant_id=sim_manifest.tenant_id,
            network_id=sim_manifest.network_id,
            version=sim_manifest.version,
            config_version="sim",
            topology_hash="",
            checksum="",
            area_id=None,
            source_uri=sim_source_uri,
            local_path=str(sim_target_root),
            status="staged",
            training_run_id=sim_manifest.training_run_id,
            training_dataset_id=sim_manifest.training_dataset_id,
            training_pipeline_commit=sim_manifest.training_pipeline_commit,
            manifest_json=json.dumps(sim_manifest.to_dict(), ensure_ascii=False),
        )
        repo.record_bundle_event(
            s,
            bundle_id=sim_manifest.sim_bundle_id,
            event_type="sim-pull",
            status="ok",
            detail=f"source={sim_source_uri} target={sim_target_root}",
            request_id=request_id,
            actor=actor,
        )

    # Compose runtime bundle from sim bundle + DB
    work_dir = Path(settings.model_dir) / "_compose"
    try:
        result = compose_runtime_bundle_from_sim_zip(
            sim_zip=sim_zip_path,
            work_dir=work_dir,
        )
    except MissingRealSnapshotError as e:
        # Real snapshot chua san sang -> giu sim bundle o status 'pending_real_snapshot'.
        # Khi controller goi PUT /internal/sync/areas/{id}/real-network sau, su kien do
        # se trigger retry tu dong (xem sync_service.sync_real_network_snapshot).
        with get_session() as s:
            repo.set_bundle_status(
                s,
                sim_manifest.sim_bundle_id,
                "pending_real_snapshot",
                rejected_reason=str(e),
            )
            repo.record_bundle_event(
                s,
                bundle_id=sim_manifest.sim_bundle_id,
                event_type="compose-deferred",
                status="pending",
                detail=str(e),
                request_id=request_id,
                actor=actor,
            )
        logger.info(
            f"[ops] Sim bundle {sim_manifest.sim_bundle_id} cho "
            f"network={sim_manifest.network_id} cho real snapshot. "
            f"Se retry tu dong khi controller upload snapshot."
        )
        raise BundleLifecycleError(str(e)) from e
    except ComposeError as e:
        raise BundleLifecycleError(str(e)) from e

    # Register runtime bundle (local zip)
    runtime_bundle = _register_bundle_from_zip(
        zip_path=result.runtime_zip,
        source_uri=sim_source_uri,
        actor=actor,
        request_id=request_id,
        auto_activate=auto_activate,
        parent_bundle_id=sim_manifest.sim_bundle_id,
    )

    _cleanup_path(result.runtime_zip)

    # Optional upload runtime bundle to MinIO for audit/redistribute
    if settings.sim_bundle_upload_runtime and settings.minio_enabled:
        runtime_uri = remote_bundle_uri(
            tenant_id=runtime_bundle.tenant_id,
            network_id=runtime_bundle.network_id,
            version=runtime_bundle.version,
            bundle_id=runtime_bundle.bundle_id,
        )
        try:
            artifact_storage.upload_uri(
                bundle_zip_path(runtime_bundle.network_id, runtime_bundle.bundle_id),
                runtime_uri,
            )
        except Exception as e:
            logger.warning(f"[ops] Upload runtime bundle failed: {e}")

    return runtime_bundle


def retry_pending_sim_bundles(
    *,
    tenant_id: str,
    network_id: str,
    actor: str = "ai-ops-retry",
) -> dict:
    """Retry compose cho cac sim bundle dang o status 'pending_real_snapshot'
    cua (tenant_id, network_id).

    Goi tu sync_service ngay sau khi controller upload real_network_snapshot.
    Idempotent: chi retry bundle pending, bundle da composed thi skip.
    """
    with get_session() as s:
        pending = repo.list_bundles(
            s,
            tenant_id=tenant_id,
            network_id=network_id,
            status="pending_real_snapshot",
            bundle_kind="sim",
        )
        pending_ids = [(b.bundle_id, b.local_path) for b in pending]

    if not pending_ids:
        return {"retried": 0, "succeeded": [], "failed": []}

    settings = get_settings()
    succeeded: list[str] = []
    failed: list[dict] = []

    for bundle_id, local_path in pending_ids:
        if not local_path or not Path(local_path).exists():
            failed.append({"bundle_id": bundle_id, "reason": "local_path khong ton tai"})
            continue
        zip_path = sim_bundle_zip_path(bundle_id)
        if not zip_path.exists():
            failed.append({"bundle_id": bundle_id, "reason": f"sim zip khong ton tai: {zip_path}"})
            continue

        work_dir = Path(settings.model_dir) / "_compose"
        try:
            result = compose_runtime_bundle_from_sim_zip(
                sim_zip=zip_path,
                work_dir=work_dir,
            )
        except MissingRealSnapshotError as e:
            failed.append({"bundle_id": bundle_id, "reason": f"van con thieu: {e}"})
            continue
        except ComposeError as e:
            with get_session() as s:
                repo.set_bundle_status(s, bundle_id, "rejected", rejected_reason=str(e))
                repo.record_bundle_event(
                    s,
                    bundle_id=bundle_id,
                    event_type="compose-retry",
                    status="failed",
                    detail=str(e),
                    actor=actor,
                )
            failed.append({"bundle_id": bundle_id, "reason": str(e)})
            continue

        try:
            runtime_bundle = _register_bundle_from_zip(
                zip_path=result.runtime_zip,
                source_uri=f"retry://sim/{bundle_id}",
                actor=actor,
                request_id=None,
                auto_activate=settings.sim_bundle_auto_activate,
                parent_bundle_id=bundle_id,
            )
            _cleanup_path(result.runtime_zip)

            with get_session() as s:
                repo.set_bundle_status(s, bundle_id, "composed")
                repo.record_bundle_event(
                    s,
                    bundle_id=bundle_id,
                    event_type="compose-retry",
                    status="ok",
                    detail=f"runtime_bundle={runtime_bundle.bundle_id}",
                    actor=actor,
                )
            succeeded.append(bundle_id)
            logger.info(
                f"[ops] Retry compose thanh cong: sim={bundle_id} "
                f"-> runtime={runtime_bundle.bundle_id}"
            )
        except BundleLifecycleError as e:
            failed.append({"bundle_id": bundle_id, "reason": str(e)})
            logger.warning(f"[ops] Retry compose fail register: {bundle_id}: {e}")

    return {
        "retried": len(pending_ids),
        "succeeded": succeeded,
        "failed": failed,
    }


def register_local_bundle(
    *,
    bundle_dir: Path,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
) -> ModelBundle:
    """Validate + register bundle da co san tren Local Storage (vd dev/test).

    `bundle_dir` phai chua model_manifest.json + cac file con. Khong move file —
    chi ghi DB.
    """
    bundle_dir = Path(bundle_dir).resolve()
    try:
        manifest = validate_bundle_dir(bundle_dir)
    except BundleValidationError as e:
        with get_session() as s:
            repo.record_bundle_event(
                s,
                bundle_id=_safe_bundle_id_from(bundle_dir),
                event_type="validate",
                status="failed",
                detail=str(e),
                request_id=request_id,
                actor=actor,
            )
        raise BundleLifecycleError(str(e)) from e

    area = _cross_validate(manifest)

    with get_session() as s:
        bundle = repo.upsert_bundle(
            s,
            bundle_id=manifest.bundle_id,
            tenant_id=manifest.tenant_id,
            network_id=manifest.network_id,
            version=manifest.version,
            config_version=manifest.config_version,
            topology_hash=manifest.topology_hash,
            checksum=manifest.checksum,
            area_id=area.area_id if area else None,
            local_path=str(bundle_dir),
            status="validated",
            training_run_id=manifest.training_run_id,
            training_dataset_id=manifest.training_dataset_id,
            training_pipeline_commit=manifest.training_pipeline_commit,
            manifest_json=json.dumps(manifest.to_dict(), ensure_ascii=False),
        )
        repo.record_bundle_event(
            s,
            bundle_id=manifest.bundle_id,
            event_type="register",
            status="ok",
            detail=f"local_path={bundle_dir}",
            request_id=request_id,
            actor=actor,
        )
    return bundle


# ----------------------------------------------------------------------
# Activate / Rollback
# ----------------------------------------------------------------------

def activate_bundle(
    bundle_id: str,
    *,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
) -> ModelBundle:
    """Mark bundle Active + ghi ActivePointer + notify ai-runtime."""
    with get_session() as s:
        bundle = repo.get_bundle(s, bundle_id)
        if bundle is None:
            raise BundleLifecycleError(f"Bundle khong ton tai: {bundle_id}")
        if bundle.status not in ("validated", "active", "rolled_back", "deprecated"):
            raise BundleLifecycleError(
                f"Bundle {bundle_id} dang o status={bundle.status}, khong activate duoc."
            )
        previous = repo.get_active_bundle(
            s, tenant_id=bundle.tenant_id, network_id=bundle.network_id
        )
        previous_id = previous.bundle_id if previous else None
        bundle = repo.activate_bundle(s, bundle_id)
        repo.record_bundle_event(
            s,
            bundle_id=bundle_id,
            event_type="activate",
            status="ok",
            detail=f"previous={previous_id}",
            request_id=request_id,
            actor=actor,
        )
        snapshot = (
            bundle.bundle_id,
            bundle.version,
            bundle.topology_hash,
            bundle.network_id,
        )

    bid, ver, topo, net = snapshot
    pointer = ActivePointer(
        bundle_id=bid,
        version=ver,
        topology_hash=topo,
        previous_bundle_id=previous_id,
    )
    write_active_pointer(network_dir(net), pointer)
    logger.info(f"[ops] Activated bundle {bid} for network {net}")

    _notify_runtime_reload(net)
    return bundle


def rollback_bundle(
    network_id: str,
    *,
    tenant_id: Optional[str] = None,
    actor: str = "ai-ops",
    request_id: Optional[str] = None,
) -> Optional[ModelBundle]:
    """Quay ve bundle truoc do (theo activated_at desc)."""
    settings = get_settings()
    tenant = tenant_id or settings.default_tenant_id
    with get_session() as s:
        current = repo.get_active_bundle(
            s, tenant_id=tenant, network_id=network_id
        )
        if current is None:
            raise BundleLifecycleError(
                f"Network {network_id} chua co bundle Active de rollback."
            )
        previous = repo.get_previous_active_bundle(
            s,
            tenant_id=tenant,
            network_id=network_id,
            exclude_bundle_id=current.bundle_id,
        )
        if previous is None:
            raise BundleLifecycleError(
                f"Network {network_id} khong co bundle truoc de rollback."
            )

        repo.set_bundle_status(
            s, current.bundle_id, "rolled_back",
            rejected_reason="rolled-back via ai-ops",
        )
        prev_activated = repo.activate_bundle(s, previous.bundle_id)
        repo.record_bundle_event(
            s,
            bundle_id=current.bundle_id,
            event_type="rollback",
            status="ok",
            detail=f"replaced_by={previous.bundle_id}",
            request_id=request_id,
            actor=actor,
        )
        repo.record_bundle_event(
            s,
            bundle_id=previous.bundle_id,
            event_type="restore",
            status="ok",
            detail=f"after_rollback_of={current.bundle_id}",
            request_id=request_id,
            actor=actor,
        )
        snapshot = (
            prev_activated.bundle_id,
            prev_activated.version,
            prev_activated.topology_hash,
            prev_activated.network_id,
            current.bundle_id,
        )

    bid, ver, topo, net, prev_id = snapshot
    pointer = ActivePointer(
        bundle_id=bid,
        version=ver,
        topology_hash=topo,
        previous_bundle_id=prev_id,
    )
    write_active_pointer(network_dir(net), pointer)
    logger.info(f"[ops] Rolled back network {net}: {prev_id} -> {bid}")

    _notify_runtime_reload(net)
    return prev_activated


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _download_to_tmp_zip(source_uri: str) -> Path:
    settings = get_settings()
    if not settings.minio_enabled:
        raise BundleLifecycleError(
            "MinIO khong duoc bat — ai-ops khong the pull bundle."
        )
    tmp_zip = settings.model_dir
    tmp_zip_path = Path(tmp_zip) / "_inflight" / f"pull_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}.zip"
    tmp_zip_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        artifact_storage.download_uri(source_uri, tmp_zip_path)
    except Exception as e:
        raise BundleLifecycleError(f"Pull that bai: {e}") from e
    return tmp_zip_path


def _register_bundle_from_zip(
    *,
    zip_path: Path,
    source_uri: str,
    actor: str,
    request_id: Optional[str],
    auto_activate: bool,
    parent_bundle_id: Optional[str] = None,
) -> ModelBundle:
    # Giai nen vao thu muc tam de doc manifest, sau do move sang vi tri chuan.
    staging_root = zip_path.parent / f"extract_{zip_path.stem}"
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        extracted = extract_bundle_zip(zip_path, staging_root)
    except BundleValidationError as e:
        _cleanup_path(staging_root)
        raise BundleLifecycleError(f"Bundle ZIP fail validate: {e}") from e

    try:
        manifest = validate_bundle_dir(extracted)
    except BundleValidationError as e:
        with get_session() as s:
            repo.record_bundle_event(
                s,
                bundle_id=_safe_bundle_id_from(extracted),
                event_type="validate",
                status="failed",
                detail=str(e),
                request_id=request_id,
                actor=actor,
            )
        _cleanup_path(staging_root)
        raise BundleLifecycleError(str(e)) from e

    # Cross-validate voi area_registry (neu da dang ky).
    area = _cross_validate(manifest)

    # Move staged bundle vao Local Model Storage chinh thuc.
    target_root = bundle_root(manifest.network_id, manifest.bundle_id)
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extracted), str(target_root))

    # Move zip vao archive.
    target_zip = bundle_zip_path(manifest.network_id, manifest.bundle_id)
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(zip_path, target_zip)
    _cleanup_path(staging_root)

    # Ghi DB.
    with get_session() as s:
        bundle = repo.upsert_bundle(
            s,
            bundle_id=manifest.bundle_id,
            bundle_kind="runtime",
            parent_bundle_id=parent_bundle_id,
            tenant_id=manifest.tenant_id,
            network_id=manifest.network_id,
            version=manifest.version,
            config_version=manifest.config_version,
            topology_hash=manifest.topology_hash,
            checksum=manifest.checksum,
            area_id=area.area_id if area else None,
            source_uri=source_uri,
            local_path=str(target_root),
            status="validated",
            training_run_id=manifest.training_run_id,
            training_dataset_id=manifest.training_dataset_id,
            training_pipeline_commit=manifest.training_pipeline_commit,
            manifest_json=json.dumps(manifest.to_dict(), ensure_ascii=False),
        )
        repo.record_bundle_event(
            s,
            bundle_id=manifest.bundle_id,
            event_type="pull",
            status="ok",
            detail=f"source={source_uri} target={target_root}",
            request_id=request_id,
            actor=actor,
        )
        repo.record_bundle_event(
            s,
            bundle_id=manifest.bundle_id,
            event_type="validate",
            status="ok",
            request_id=request_id,
            actor=actor,
        )
    logger.info(
        f"[ops] Pulled bundle {manifest.bundle_id} "
        f"(network={manifest.network_id}, version={manifest.version})"
    )

    if auto_activate:
        try:
            bundle = activate_bundle(
                manifest.bundle_id, actor=actor, request_id=request_id
            )
        except BundleLifecycleError as e:
            logger.warning(
                f"[ops] auto_activate failed for {manifest.bundle_id}: {e}. "
                f"Bundle van o status='validated', co the activate manual."
            )

    return bundle

def _cross_validate(manifest: ModelManifest) -> Optional[AreaRegistry]:
    """Doi chieu network_id voi area_registry. Neu chua co area, log canh bao."""
    with get_session() as s:
        area = repo.get_area_by_network(s, manifest.tenant_id, manifest.network_id)
        if area is None:
            # Area chua dang ky. Cho phep tiep tuc — vai workflow dang ky bundle truoc.
            logger.warning(
                f"[ops] Bundle {manifest.bundle_id}: chua co area_registry "
                f"khop tenant={manifest.tenant_id} network={manifest.network_id}."
            )
            return None
        if not area.is_active:
            raise BundleLifecycleError(
                f"Area {area.area_id} bi inactive — tu choi activate."
            )
        return area


def _safe_bundle_id_from(path: Path) -> str:
    """Best-effort doc bundle_id de log fail-event ngay khi validate fail."""
    manifest_path = path / "model_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("bundle_id", "unknown"))
        except Exception:
            return "unknown"
    return "unknown"


def _cleanup_path(p: Path) -> None:
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Cleanup failed {p}: {e}")


def _notify_runtime_reload(network_id: str) -> None:
    """Goi ai-runtime hot-reload qua HTTP (best effort)."""
    settings = get_settings()
    url_base = settings.runtime_internal_url
    if not url_base:
        return  # cung process hoac chua wire — runtime se tu poll active.json.
    try:
        import httpx  # type: ignore
        endpoint = url_base.rstrip("/") + "/internal/runtime/reload"
        httpx.post(endpoint, json={"network_id": network_id}, timeout=2.0)
    except Exception as e:
        logger.warning(f"[ops] notify runtime reload failed: {e}")
