"""Audit logging cho inference (plan 4.2.5 + Lop 2 RLOps)."""

from __future__ import annotations

from typing import Optional

from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session


def record_inference(
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
) -> None:
    try:
        with get_session() as session:
            repo.record_inference_audit(
                session,
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
    except Exception as e:
        # Audit khong duoc lam hong response -> chi log.
        logger.warning(f"Khong ghi duoc inference_audit (request_id={request_id}): {e}")
