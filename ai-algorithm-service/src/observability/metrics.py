"""Prometheus metrics + helper.

Phu thuoc `prometheus-client` (optional). Neu chua cai, cac function tro thanh
no-op de service van chay binh thuong (Observability la add-on).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    _PROM_OK = True
except Exception:  # pragma: no cover
    CONTENT_TYPE_LATEST = "text/plain"
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    generate_latest = lambda: b""  # type: ignore[assignment]
    _PROM_OK = False


if _PROM_OK:
    INFERENCE_TOTAL = Counter(
        "ai_inference_total",
        "So request inference da xu ly",
        ["role", "status"],
    )
    INFERENCE_LATENCY_MS = Histogram(
        "ai_inference_latency_ms",
        "Latency inference (ms) — bao gom guardrails",
        ["role"],
        buckets=(5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000),
    )
    GUARDRAIL_VIOLATIONS_TOTAL = Counter(
        "ai_guardrail_violations_total",
        "So vi pham guardrails theo rule",
        ["rule"],
    )
    BUNDLE_ACTIVE_INFO = Gauge(
        "ai_bundle_active_info",
        "Active bundle info (gauge=1 cho moi label set hien hanh)",
        ["network_id", "bundle_id", "version"],
    )
    DRIFT_EVENTS_TOTAL = Counter(
        "ai_drift_events_total",
        "So drift event da phat hien",
        ["network_id", "method", "severity"],
    )
else:  # pragma: no cover
    INFERENCE_TOTAL = INFERENCE_LATENCY_MS = None  # type: ignore[assignment]
    GUARDRAIL_VIOLATIONS_TOTAL = BUNDLE_ACTIVE_INFO = None  # type: ignore[assignment]
    DRIFT_EVENTS_TOTAL = None  # type: ignore[assignment]


def register_metrics(app) -> None:
    """Mount /metrics endpoint vao FastAPI `app`. Bo qua neu prometheus-client thieu."""
    if not _PROM_OK:
        return

    @app.get("/metrics", include_in_schema=False)
    def _metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_inference_metric(
    *,
    role: str,
    status: str,
    latency_ms: Optional[float] = None,
) -> None:
    if not _PROM_OK:
        return
    INFERENCE_TOTAL.labels(role=role, status=status).inc()
    if latency_ms is not None:
        INFERENCE_LATENCY_MS.labels(role=role).observe(float(latency_ms))


def record_guardrail_violation(rule: str) -> None:
    if not _PROM_OK:
        return
    GUARDRAIL_VIOLATIONS_TOTAL.labels(rule=rule).inc()


def set_active_bundle_info(*, network_id: str, bundle_id: str, version: str) -> None:
    if not _PROM_OK:
        return
    # Reset cac label set khac cho cung network -> set 0. Implementation toi gian:
    # Khong reset ten cu, chap nhan multiple time series — Prometheus query
    # dung `topk(1, ai_bundle_active_info{network_id="..."})` la du.
    BUNDLE_ACTIVE_INFO.labels(
        network_id=network_id, bundle_id=bundle_id, version=version
    ).set(1)


def record_drift_metric(*, network_id: str, method: str, severity: str) -> None:
    if not _PROM_OK:
        return
    DRIFT_EVENTS_TOTAL.labels(
        network_id=network_id, method=method, severity=severity
    ).inc()
