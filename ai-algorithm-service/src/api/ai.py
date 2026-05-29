"""Public API cho control software (plan 5.1 + 11.3).

Endpoints:
  GET  /api/algorithm/ai/areas                     — manifest rut gon (plan 11.3.3)
  GET  /api/algorithm/ai/areas/{id}/readiness      — chi tiet readiness (plan 5.1.2)
  GET  /api/algorithm/ai/areas/{id}/network        — network.json cua area
  GET  /api/algorithm/ai/areas/{id}/intersections/{cid}/config
  PUT  /api/algorithm/ai/areas/{id}/intersections/{cid}/config
  POST /api/algorithm/ai                           — inference (strict/readiness guard)
  POST /api/algorithm/ai/cache/clear               — reload cache (plan 6.2.3)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request

from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.preprocessing import (
    IntersectionConfig,
    get_config,
    load_network,
    save_config,
)
from src.preprocessing.intersection_registry import clear_cache as clear_config_cache
from src.schemas.ai_schemas.ai_input import AIInput
from src.schemas.ai_schemas.ai_output import AIOutput
from src.services.ai_service import AIService
from src.services.model_manager import clear_cache as clear_policy_cache
from src.services.readiness_service import check_area, list_visible_manifest

router = APIRouter()


_DESC_PATH = Path("api_docs/run_ai_algorithm.md")
_RUN_DESC = _DESC_PATH.read_text(encoding="utf-8") if _DESC_PATH.exists() else "AI Algorithm API"


@router.get("/api/algorithm/ai/areas")
def get_available_areas():
    """Manifest rut gon cho UI: chi area active + visible + ready."""
    return {"areas": list_visible_manifest()}


@router.get("/api/algorithm/ai/areas/{area_id}/readiness")
def get_area_readiness(area_id: int):
    """Chi tiet readiness cua 1 area (hasPolicy/hasMeta/hasNetwork + version)."""
    return check_area(area_id).to_dict()


@router.get("/api/algorithm/ai/areas/{area_id}/network")
def get_area_network(area_id: int):
    net = load_network(area_id)
    if net is None:
        raise AlgorithmException(
            f"Area {area_id} chua co network.json.",
            code=ErrorCode.CONFIG_NOT_FOUND,
            area_id=area_id,
        )
    return net


@router.get("/api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config")
def get_intersection_config(area_id: int, cross_id: int):
    cfg = get_config(area_id, cross_id)
    if cfg is None:
        raise AlgorithmException(
            f"Chua co config cho area={area_id} cross={cross_id}.",
            code=ErrorCode.CONFIG_NOT_FOUND,
            area_id=area_id,
        )
    return cfg.to_dict()


@router.put("/api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config")
def upsert_intersection_config(area_id: int, cross_id: int, payload: dict):
    """Override intersection config (manual)."""
    payload = dict(payload or {})
    payload["cross_id"] = cross_id
    try:
        cfg = IntersectionConfig.from_dict(payload)
    except Exception as e:
        raise AlgorithmException(
            f"Config khong hop le: {e}",
            code=ErrorCode.INVALID_INPUT,
            area_id=area_id,
        )
    path = save_config(area_id, cfg)
    return {"areaId": area_id, "crossId": cross_id, "path": str(path), "status": "saved"}


@router.post(
    "/api/algorithm/ai",
    description=_RUN_DESC,
    response_model=AIOutput,
)
def run_ai_algorithm(ai_input: AIInput, request: Request):
    """Inference cho 1 area (enforce 1 area/request neu bat cau hinh)."""
    request_id = getattr(request.state, "request_id", "")
    area_ids = sorted({c.areaId for c in ai_input.crosses})
    logger.info(
        f"request_id={request_id} RunAI crosses={len(ai_input.crosses)} areas={area_ids}"
    )
    return AIService(ai_input).run(ai_input, request_id=request_id)


@router.post("/api/algorithm/ai/cache/clear")
def clear_caches(area_id: int | None = Query(default=None)):
    """Xoa in-memory cache. Neu `area_id` co -> chi area do, else tat ca."""
    clear_config_cache(area_id)
    clear_policy_cache(area_id)
    return {"status": "cleared", "areaId": area_id}
