"""Observability layer (Lop 4).

Bao gom:
  - metrics.py: Prometheus exporter (counters, histograms, gauges).
  - drift.py:   PSI + KS-test drift detection.
  - mlflow_helper.py: Tracking + Model Registry (Lop 3 slice).

Cac metric name follow Prometheus convention:
  ai_inference_total{role,status,...}
  ai_inference_latency_ms{role}
  ai_guardrail_violations_total{rule}
  ai_bundle_active_info{network_id,bundle_id,version}
"""

from src.observability.metrics import (
    BUNDLE_ACTIVE_INFO,
    DRIFT_EVENTS_TOTAL,
    GUARDRAIL_VIOLATIONS_TOTAL,
    INFERENCE_LATENCY_MS,
    INFERENCE_TOTAL,
    register_metrics,
    set_active_bundle_info,
    record_inference_metric,
    record_guardrail_violation,
)
from src.observability.drift import (
    DriftCheckResult,
    DriftDetector,
    ks_statistic,
    psi,
)

__all__ = [
    "BUNDLE_ACTIVE_INFO",
    "DRIFT_EVENTS_TOTAL",
    "DriftCheckResult",
    "DriftDetector",
    "GUARDRAIL_VIOLATIONS_TOTAL",
    "INFERENCE_LATENCY_MS",
    "INFERENCE_TOTAL",
    "ks_statistic",
    "psi",
    "record_guardrail_violation",
    "record_inference_metric",
    "register_metrics",
    "set_active_bundle_info",
]
