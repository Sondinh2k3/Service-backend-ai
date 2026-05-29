"""ai-runtime (Container 2.2) — Inference Service.

Trach nhiem:
  - Preflight check khi swap bundle Active.
  - Pipeline inference 4 buoc: Topology Normalizer -> ONNX -> Phase Normalizer -> Guardrails.
  - Theo doi active.json (poll TTL) de hot-reload model.
  - Tracker anti-starvation + drift sample collector.

Khong push file len Artifact Store. Khong sua DB metadata bundle (tru audit).
"""

from src.runtime.guardrails import (
    GuardrailDecision,
    GuardrailReport,
    GuardrailViolation,
    apply_guardrails,
)
from src.runtime.preflight import PreflightError, run_preflight
from src.runtime.starvation import StarvationTracker, get_starvation_tracker

__all__ = [
    "GuardrailDecision",
    "GuardrailReport",
    "GuardrailViolation",
    "PreflightError",
    "StarvationTracker",
    "apply_guardrails",
    "get_starvation_tracker",
    "run_preflight",
]
