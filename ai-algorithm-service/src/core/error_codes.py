"""Business error codes (plan 5.3).

Bat buoc contract error chuan giua service va control software de client xu ly
dep theo enum, khong phu thuoc message tieng Viet.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # Nghiep vu area/policy (plan 5.3)
    AREA_NOT_FOUND = "AREA_NOT_FOUND"
    AREA_NOT_ACTIVE = "AREA_NOT_ACTIVE"
    AREA_NOT_READY = "AREA_NOT_READY"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_CONTRACT_MISMATCH = "POLICY_CONTRACT_MISMATCH"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    VERSION_MISMATCH = "VERSION_MISMATCH"

    # Request / input
    INVALID_INPUT = "INVALID_INPUT"
    MULTIPLE_AREAS_NOT_ALLOWED = "MULTIPLE_AREAS_NOT_ALLOWED"
    UNAUTHORIZED = "UNAUTHORIZED"

    # System
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SYNC_IDEMPOTENCY_CONFLICT = "SYNC_IDEMPOTENCY_CONFLICT"


# HTTP status mapping cho moi error code.
HTTP_STATUS_BY_CODE = {
    ErrorCode.AREA_NOT_FOUND: 404,
    ErrorCode.AREA_NOT_ACTIVE: 409,
    ErrorCode.AREA_NOT_READY: 409,
    ErrorCode.POLICY_NOT_FOUND: 404,
    ErrorCode.POLICY_CONTRACT_MISMATCH: 409,
    ErrorCode.CONFIG_NOT_FOUND: 404,
    ErrorCode.VERSION_MISMATCH: 409,
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.MULTIPLE_AREAS_NOT_ALLOWED: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SYNC_IDEMPOTENCY_CONFLICT: 409,
}


def http_status_for(code: ErrorCode) -> int:
    return HTTP_STATUS_BY_CODE.get(code, 400)
