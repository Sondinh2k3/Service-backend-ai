"""Telemetry middleware (plan 6.2.4).

Gan request_id vao request.state de handler/service co the chen vao log + response
audit. Do latency va ghi log chuan cho moi request.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.config import get_settings
from src.core.logger import logger


class TelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        header = settings.request_id_header

        request_id = request.headers.get(header) or str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.start_time = time.perf_counter()

        response: Response
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = int((time.perf_counter() - request.state.start_time) * 1000)
            logger.exception(
                f"request_id={request_id} method={request.method} path={request.url.path} "
                f"status=500 latency_ms={latency_ms}"
            )
            raise

        latency_ms = int((time.perf_counter() - request.state.start_time) * 1000)
        response.headers[header] = request_id
        if settings.telemetry_enabled:
            logger.info(
                f"request_id={request_id} method={request.method} path={request.url.path} "
                f"status={response.status_code} latency_ms={latency_ms}"
            )
        return response
