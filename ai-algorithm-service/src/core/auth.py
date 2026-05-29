"""API-key based auth for internal endpoints (plan 6.2.1).

Dev mode (internal_api_key khong set) -> bo qua kiem tra. Production nen set.
"""

from __future__ import annotations

from fastapi import Request

from src.core.config import get_settings
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException


def require_internal_api_key(request: Request) -> None:
    """FastAPI dependency — raise 401 neu thieu hoac sai API key."""
    settings = get_settings()
    expected = settings.internal_api_key
    if not expected:
        # Khong cau hinh -> bo qua (dev mode).
        return

    provided = request.headers.get(settings.internal_api_key_header)
    if provided != expected:
        raise AlgorithmException(
            "Missing or invalid internal API key.",
            code=ErrorCode.UNAUTHORIZED,
        )
