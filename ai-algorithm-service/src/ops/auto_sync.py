"""Auto-sync bundle moi tu MinIO.

Ket hop 2 co che de instant + reliable:
  1. Listener (instant): MinIO listen_bucket_notification S3 SDK long-poll
     stream. Chay trong thread rieng vi MinIO SDK la sync. Outbound HTTPS only
     -> xuyen NAT/firewall thoai mai.
  2. Safety-net poller: async task scan bucket dinh ky (~10 phut). Bat su kien
     bi miss khi listener disconnect/restart.

Ca 2 deu goi `_handle_uri()` — cung idempotent + lock per URI de tranh race.

Lifecycle:
  start()  - khoi tao threads + tasks. Goi tu FastAPI lifespan.
  stop()   - graceful shutdown.
  status() - snapshot trang thai cho /ops/auto-sync/status.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Set

from src.core.config import get_settings
from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session
from src.ops import lifecycle
from src.services import artifact_storage


@dataclass
class _AutoSyncState:
    enabled: bool = False
    started_at: Optional[float] = None
    listener_alive: bool = False
    listener_reconnects: int = 0
    listener_last_event_at: Optional[float] = None
    poller_alive: bool = False
    poller_runs: int = 0
    poller_last_run_at: Optional[float] = None
    in_progress: Set[str] = field(default_factory=set)
    pulled_count: int = 0
    failed_count: int = 0
    last_error: Optional[str] = None


_state = _AutoSyncState()
_state_lock = threading.Lock()
_stop_event = threading.Event()
_listener_thread: Optional[threading.Thread] = None
_poller_task: Optional[asyncio.Task] = None


def _is_already_known(source_uri: str) -> bool:
    """Return True neu URI da co ban ghi trong model_bundle (any status)."""
    try:
        with get_session() as s:
            return repo.bundle_exists_by_source_uri(s, source_uri)
    except Exception as e:
        logger.warning(f"[auto-sync] DB check fail for {source_uri}: {e}")
        return False


def _handle_uri(source_uri: str, *, actor: str) -> None:
    """Pull + auto-activate 1 URI. Idempotent + lock per URI."""
    settings = get_settings()
    with _state_lock:
        if source_uri in _state.in_progress:
            return  # dang xu ly tu source khac (listener vs poller)
        _state.in_progress.add(source_uri)

    try:
        if _is_already_known(source_uri):
            return  # da pull truoc do, skip
        logger.info(f"[auto-sync] Pulling new bundle: {source_uri}")
        if settings.sim_bundle_auto_compose_enabled:
            lifecycle.pull_and_register_bundle_auto(
                source_uri=source_uri,
                actor=actor,
                auto_activate=settings.sim_bundle_auto_activate,
            )
        else:
            lifecycle.pull_and_register_bundle(
                source_uri=source_uri,
                actor=actor,
                auto_activate=settings.minio_auto_sync_auto_activate,
            )
        with _state_lock:
            _state.pulled_count += 1
    except lifecycle.BundleLifecycleError as e:
        with _state_lock:
            _state.failed_count += 1
            _state.last_error = f"{source_uri}: {e}"
        logger.warning(f"[auto-sync] Pull failed for {source_uri}: {e}")
    except Exception as e:
        with _state_lock:
            _state.failed_count += 1
            _state.last_error = f"{source_uri}: {e}"
        logger.exception(f"[auto-sync] Pull failed for {source_uri}: {e}")
    finally:
        with _state_lock:
            _state.in_progress.discard(source_uri)


# ----------------------------------------------------------------------
# Listener (instant via MinIO long-poll)
# ----------------------------------------------------------------------

def _listener_loop() -> None:
    """Long-poll MinIO bucket notification. Reconnect khi disconnect."""
    settings = get_settings()
    backoff = settings.minio_auto_sync_reconnect_seconds
    max_backoff = max(60, backoff * 8)
    prefix, suffix = _auto_sync_prefix_suffix(settings)

    while not _stop_event.is_set():
        with _state_lock:
            _state.listener_alive = True
        try:
            iterator = artifact_storage.listen_remote_zips(
                prefix=prefix,
                suffix=suffix,
                events=("s3:ObjectCreated:*",),
            )
            for source_uri, _record in iterator:
                if _stop_event.is_set():
                    break
                with _state_lock:
                    _state.listener_last_event_at = time.time()
                _handle_uri(source_uri, actor="auto-sync-listener")
            # Iterator return binh thuong (server close stream) — reconnect.
            if _stop_event.is_set():
                break
            backoff = settings.minio_auto_sync_reconnect_seconds  # reset
        except Exception as e:
            with _state_lock:
                _state.listener_reconnects += 1
                _state.last_error = f"listener: {e}"
            logger.warning(
                f"[auto-sync] Listener disconnect ({e}), reconnect after {backoff}s"
            )
            if _stop_event.wait(timeout=backoff):
                break
            backoff = min(max_backoff, backoff * 2)  # exponential

    with _state_lock:
        _state.listener_alive = False
    logger.info("[auto-sync] Listener stopped.")


# ----------------------------------------------------------------------
# Safety-net poller
# ----------------------------------------------------------------------

async def _poller_loop() -> None:
    """Async task: scan bucket dinh ky, bat su kien bi miss."""
    settings = get_settings()
    interval = settings.minio_auto_sync_poll_interval_seconds
    prefix, suffix = _auto_sync_prefix_suffix(settings)
    if interval <= 0:
        logger.info("[auto-sync] Poller disabled (interval=0).")
        return

    with _state_lock:
        _state.poller_alive = True
    try:
        while not _stop_event.is_set():
            try:
                uris = await asyncio.to_thread(
                    artifact_storage.list_remote_zips,
                    prefix,
                    suffix,
                )
                with _state_lock:
                    _state.poller_runs += 1
                    _state.poller_last_run_at = time.time()
                for uri in uris:
                    if _stop_event.is_set():
                        break
                    await asyncio.to_thread(
                        _handle_uri, uri, actor="auto-sync-poller"
                    )
            except Exception as e:
                with _state_lock:
                    _state.last_error = f"poller: {e}"
                logger.warning(f"[auto-sync] Poller iteration failed: {e}")

            # Sleep with cancellation support.
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(_stop_event.wait, interval),
                    timeout=interval + 5,
                )
            except asyncio.TimeoutError:
                pass
            if _stop_event.is_set():
                break
    finally:
        with _state_lock:
            _state.poller_alive = False
    logger.info("[auto-sync] Poller stopped.")


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def start() -> None:
    """Khoi tao listener thread + poller task. Goi tu lifespan startup."""
    global _listener_thread, _poller_task

    settings = get_settings()
    if not settings.minio_auto_sync_enabled:
        logger.info("[auto-sync] Disabled (MINIO_AUTO_SYNC_ENABLED=false).")
        return
    if not settings.minio_enabled:
        logger.warning(
            "[auto-sync] Bat tat: MinIO disabled. Bo qua auto-sync."
        )
        return
    if _listener_thread is not None:
        return  # already started

    # Safety check: prefix sim_bundle phai khac prefix runtime bundle.
    # Neu giong nhau, composer upload runtime bundle len MinIO se trigger
    # listener nay (vong lap vo han). Khi `sim_bundle_upload_runtime=True`,
    # day la lan duy nhat ai-ops upload tro lai, nen overlap rat de gay loi.
    _check_prefix_safety(settings)

    _stop_event.clear()
    with _state_lock:
        _state.enabled = True
        _state.started_at = time.time()

    # Listener thread (sync MinIO SDK).
    _listener_thread = threading.Thread(
        target=_listener_loop,
        name="auto-sync-listener",
        daemon=True,
    )
    _listener_thread.start()

    # Safety-net poller (async task).
    try:
        loop = asyncio.get_running_loop()
        _poller_task = loop.create_task(_poller_loop(), name="auto-sync-poller")
    except RuntimeError:
        # No running loop — likely called outside lifespan. Skip poller.
        logger.warning("[auto-sync] No event loop, poller skipped.")

    prefix, suffix = _auto_sync_prefix_suffix(settings)
    logger.info(
        f"[auto-sync] Started. prefix={prefix or '(all)'} "
        f"suffix={suffix} "
        f"poll_interval={settings.minio_auto_sync_poll_interval_seconds}s"
    )


async def stop() -> None:
    """Graceful shutdown. Goi tu lifespan teardown."""
    global _listener_thread, _poller_task

    _stop_event.set()

    if _poller_task is not None:
        _poller_task.cancel()
        try:
            await _poller_task
        except (asyncio.CancelledError, Exception):
            pass
        _poller_task = None

    if _listener_thread is not None:
        # Khong join — listener block trong long-poll, daemon=True nen process
        # exit khong bi keo. Chi cho 2s de log "stopped".
        _listener_thread.join(timeout=2.0)
        _listener_thread = None

    with _state_lock:
        _state.enabled = False


def status() -> dict:
    """Snapshot debug. Goi tu /ops/auto-sync/status."""
    with _state_lock:
        return {
            "enabled": _state.enabled,
            "started_at": _state.started_at,
            "sim_bundle_auto_compose": get_settings().sim_bundle_auto_compose_enabled,
            "listener": {
                "alive": _state.listener_alive,
                "reconnects": _state.listener_reconnects,
                "last_event_at": _state.listener_last_event_at,
            },
            "poller": {
                "alive": _state.poller_alive,
                "runs": _state.poller_runs,
                "last_run_at": _state.poller_last_run_at,
            },
            "in_progress": sorted(_state.in_progress),
            "pulled_count": _state.pulled_count,
            "failed_count": _state.failed_count,
            "last_error": _state.last_error,
        }


def _auto_sync_prefix_suffix(settings):
    if settings.sim_bundle_auto_compose_enabled:
        prefix = settings.sim_bundle_prefix or settings.minio_auto_sync_prefix
        suffix = settings.sim_bundle_suffix or settings.minio_auto_sync_suffix
        return prefix, suffix
    return settings.minio_auto_sync_prefix, settings.minio_auto_sync_suffix


def _check_prefix_safety(settings) -> None:
    """Phat hien cau hinh prefix gay vong lap khi auto-upload runtime bundle.

    Cu the: khi `sim_bundle_auto_compose_enabled=True` va
    `sim_bundle_upload_runtime=True`, runtime bundle se duoc upload bang
    `remote_bundle_uri()` (su dung `artifact_bundle_prefix`). Listener auto-sync
    o day filter theo `sim_bundle_prefix`. Neu hai prefix nay overlap, listener
    se pickup runtime bundle vua upload va co gang compose lai -> fail.

    Khong raise — chi log warning de operator chu y. Production phai dat:
      - sim_bundle_prefix      = 'sim/'        (sim bundle CI/CD upload)
      - artifact_bundle_prefix = 'runtime/'    (composer upload tro lai)
    va suffix sim_bundle_suffix='.sim.zip' lam lop bao ve thu hai.
    """
    if not (settings.sim_bundle_auto_compose_enabled and settings.sim_bundle_upload_runtime):
        return

    sim_prefix = (settings.sim_bundle_prefix or settings.minio_auto_sync_prefix or "").strip("/")
    runtime_prefix = (settings.artifact_bundle_prefix or "").strip("/")

    if not sim_prefix and not runtime_prefix:
        logger.warning(
            "[auto-sync] CANH BAO: ca sim_bundle_prefix va artifact_bundle_prefix "
            "deu rong. Listener se pickup ca runtime bundle vua upload -> vong lap. "
            "Hay dat sim_bundle_prefix='sim/' va artifact_bundle_prefix='runtime/'."
        )
        return

    # Overlap khi mot prefix la tien to cua cai con lai.
    if sim_prefix and runtime_prefix:
        if sim_prefix == runtime_prefix or \
           sim_prefix.startswith(runtime_prefix + "/") or \
           runtime_prefix.startswith(sim_prefix + "/"):
            logger.warning(
                f"[auto-sync] CANH BAO: sim_bundle_prefix='{sim_prefix}' overlap voi "
                f"artifact_bundle_prefix='{runtime_prefix}'. Khi composer upload "
                f"runtime bundle tro lai MinIO, listener nay co the pickup -> vong lap. "
                f"Hay tach 2 prefix rieng. Suffix '{settings.sim_bundle_suffix}' van "
                f"hoat dong nhu lop bao ve thu hai neu runtime bundle khong ket thuc "
                f"bang suffix nay."
            )

    # Yen tam neu runtime bundle khong ket thuc bang sim_bundle_suffix (vd
    # '.sim.zip' vs '.zip' thuong).
    sim_suffix = settings.sim_bundle_suffix or ""
    if sim_suffix == ".zip":
        logger.warning(
            "[auto-sync] CANH BAO: sim_bundle_suffix='.zip' qua chung chung. "
            "Hay dat thanh '.sim.zip' de phan biet voi runtime bundle."
        )
