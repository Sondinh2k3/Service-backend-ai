"""ai-runtime internal API — chu yeu de ai-ops trigger hot-reload.

Public inference API van o `src/api/ai.py`. File nay chi de internal/admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.bundles import network_dir, read_active_pointer
from src.core.auth import require_internal_api_key
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.observability import drift_registry
from src.preprocessing.feature_builder import clear_cache as clear_feature_builder_cache
from src.preprocessing.intersection_registry import clear_cache as clear_config_cache
from src.runtime.bundle_resolver import invalidate_network
from src.runtime.preflight import PreflightError, run_preflight
from src.runtime.starvation import get_starvation_tracker
from src.services.model_manager import clear_cache as clear_policy_cache


router = APIRouter(
    prefix="/internal/runtime",
    dependencies=[Depends(require_internal_api_key)],
    tags=["ai-runtime-internal"],
)


class ReloadRequest(BaseModel):
    network_id: str = Field(..., description="Network can hot-reload bundle.")
    runPreflight: bool = Field(default=True)


@router.post("/reload")
def reload_network(body: ReloadRequest):
    """Invalidate cache + chay Preflight cho bundle Active moi."""
    network_id = body.network_id
    invalidate_network(network_id)
    # Clear policy cache cho moi area cua network. Don gian: clear tat ca.
    clear_policy_cache(None)
    clear_config_cache(None)
    clear_feature_builder_cache(None)
    # Reset drift detector — baseline cu khong con apply cho bundle moi.
    drift_registry.reset_detector(network_id)

    info = {"networkId": network_id, "preflight": "skipped"}
    if body.runPreflight:
        try:
            pointer, manifest = run_preflight(network_id)
            info["preflight"] = "ok"
            info["bundleId"] = pointer.bundle_id
            info["version"] = manifest.version
        except PreflightError as e:
            logger.error(f"[runtime] Preflight fail network={network_id}: {e}")
            raise AlgorithmException(
                f"Preflight fail: {e}",
                code=ErrorCode.AREA_NOT_READY,
            ) from e

    return {"status": "reloaded", **info}


@router.get("/active/{network_id}")
def runtime_active(network_id: str):
    pointer = read_active_pointer(network_dir(network_id))
    if pointer is None:
        raise AlgorithmException(
            f"Network {network_id} chua co active.json.",
            code=ErrorCode.AREA_NOT_READY,
        )
    return pointer.to_dict()


@router.get("/starvation")
def starvation_snapshot():
    return {"counts": get_starvation_tracker().snapshot()}


@router.get("/drift")
def drift_snapshot():
    """Trang thai cua tat ca DriftDetector (baseline/window sizes, counter)."""
    return {"detectors": drift_registry.snapshot()}
