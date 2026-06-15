from __future__ import annotations

from typing import Any, Literal

from src.core.logger import logger

LogLevel = Literal["debug", "info", "warning", "error", "exception"]


def log_event(
    event: str,
    *,
    message: str | None = None,
    level: LogLevel = "info",
    request_id: str | None = None,
    status: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured business trace event.

    Keep this helper best-effort and side-effect only: logging must not become
    part of the request's critical path.
    """
    payload = {
        "event": event,
        **({"request_id": request_id} if request_id else {}),
        **({"event_status": status} if status else {}),
        **{k: v for k, v in fields.items() if v is not None},
    }
    text = message or event
    log_method = getattr(logger, level)
    log_method(text, extra=payload)
