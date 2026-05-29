"""Centralized exception handling (plan 5.3, 6.1.4).

Moi loi tra ve chung 1 schema:
    {
      "errorCode": "AREA_NOT_FOUND",
      "message": "...",
      "areaId": 12,           # optional
      "requestId": "...",     # optional
      "path": "/api/..."
    }

AlgorithmException giu lai ten cu de khong break callsite noi bo, nhung them
`code: ErrorCode` de client map duoc.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from src.core.error_codes import ErrorCode, http_status_for
from src.core.logger import logger


class AlgorithmException(Exception):
    """Loi nghiep vu AI service — luon duoc serialize theo error contract."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INVALID_INPUT,
        *,
        area_id: Optional[int] = None,
        http_status: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.area_id = area_id
        self.http_status = http_status or http_status_for(code)
        self.extra = extra or {}


def _error_payload(
    *,
    code: str,
    message: str,
    request: Request,
    area_id: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "errorCode": code,
        "message": message,
        "path": str(request.url.path),
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        payload["requestId"] = request_id
    if area_id is not None:
        payload["areaId"] = area_id
    if extra:
        payload.update(extra)
    return payload


def algorithm_exception_handler(request: Request, exc: AlgorithmException):
    logger.warning(
        f"AlgorithmException at {request.url.path}: code={exc.code.value} "
        f"message={exc.message} areaId={exc.area_id}"
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_payload(
            code=exc.code.value,
            message=exc.message,
            request=request,
            area_id=exc.area_id,
            extra=exc.extra,
        ),
    )


def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error at {request.url.path}: {exc.errors()}")
    errors = [
        {
            "field": ".".join(map(str, err["loc"])),
            "message": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            code=ErrorCode.INVALID_INPUT.value,
            message="Request validation failed.",
            request=request,
            extra={"details": errors},
        ),
    )


def http_exception_handler(request: Request, exc: HTTPException):
    # Map HTTPException -> error contract. Client code thuong la HTTPException
    # do auth/404, nen default map sang INVALID_INPUT khi khong co info ro rang.
    if exc.status_code == 401:
        code = ErrorCode.UNAUTHORIZED
    elif exc.status_code == 404:
        code = ErrorCode.AREA_NOT_FOUND
    else:
        code = ErrorCode.INVALID_INPUT
    logger.warning(f"HTTPException at {request.url.path}: status={exc.status_code} detail={exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            code=code.value,
            message=str(exc.detail) if exc.detail else "HTTP error",
            request=request,
        ),
    )


def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error at {request.url.path}")
    return JSONResponse(
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload(
            code=ErrorCode.INTERNAL_ERROR.value,
            message="Unexpected error occurred",
            request=request,
        ),
    )
