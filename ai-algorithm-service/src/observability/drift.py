"""Drift detection — PSI + KS-test (tu viet, khong phu thuoc evidently).

Dung cho:
  - Data drift: phan phoi traffic feature thay doi vs baseline.
  - Performance drift: phan phoi reward / latency / action thay doi.

PSI (Population Stability Index):
  PSI = sum_i (p_i - q_i) * ln(p_i / q_i)
  - PSI < 0.1: stable
  - 0.1 <= PSI < 0.2: minor drift
  - PSI >= 0.2: major drift (default threshold)

KS (Kolmogorov-Smirnov):
  D = max |F_p(x) - F_q(x)|
  - D nho: phan phoi tuong dong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

import numpy as np

from src.core.config import get_settings
from src.core.logger import logger
from src.db import repositories as repo
from src.db.base import get_session
from src.observability import metrics as metrics_mod


_DEFAULT_BINS = 10
_EPS = 1e-6


def _to_array(x: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(x), dtype=np.float64)
    return arr[np.isfinite(arr)]


def psi(
    expected: Iterable[float],
    actual: Iterable[float],
    *,
    bins: int = _DEFAULT_BINS,
) -> float:
    """Population Stability Index. Nho hon nguong (vd 0.2) -> stable."""
    e = _to_array(expected)
    a = _to_array(actual)
    if e.size == 0 or a.size == 0:
        return float("nan")

    edges = np.quantile(e, np.linspace(0, 1, bins + 1))
    # Dam bao monotonic (handle constant array).
    edges = np.unique(edges)
    if edges.size < 2:
        return 0.0
    edges[0] = -np.inf
    edges[-1] = np.inf

    e_hist, _ = np.histogram(e, bins=edges)
    a_hist, _ = np.histogram(a, bins=edges)

    e_perc = e_hist / max(1, e_hist.sum())
    a_perc = a_hist / max(1, a_hist.sum())
    e_perc = np.clip(e_perc, _EPS, None)
    a_perc = np.clip(a_perc, _EPS, None)

    return float(np.sum((a_perc - e_perc) * np.log(a_perc / e_perc)))


def ks_statistic(expected: Iterable[float], actual: Iterable[float]) -> float:
    """KS statistic D = max|F_e(x) - F_a(x)| (khong tra p-value)."""
    e = np.sort(_to_array(expected))
    a = np.sort(_to_array(actual))
    if e.size == 0 or a.size == 0:
        return float("nan")

    all_v = np.sort(np.concatenate([e, a]))
    cdf_e = np.searchsorted(e, all_v, side="right") / e.size
    cdf_a = np.searchsorted(a, all_v, side="right") / a.size
    return float(np.max(np.abs(cdf_e - cdf_a)))


@dataclass
class DriftCheckResult:
    feature: str
    method: str
    score: float
    threshold: float
    triggered: bool
    severity: str = "warn"


@dataclass
class DriftDetector:
    """In-memory baseline + sliding window samples cho 1 network.

    Dung cho MVP. Khi can persistent, swap _baseline + _window sang Redis.
    """
    network_id: str
    baseline: dict = field(default_factory=dict)  # feature -> List[float]
    window: dict = field(default_factory=dict)    # feature -> List[float]
    bundle_id: Optional[str] = None

    def set_baseline(self, feature: str, samples: Sequence[float]) -> None:
        self.baseline[feature] = list(samples)
        self.window[feature] = []

    def observe(self, feature: str, value: float) -> None:
        if feature not in self.baseline:
            return
        self.window.setdefault(feature, []).append(float(value))

    def check(self) -> List[DriftCheckResult]:
        settings = get_settings()
        results: List[DriftCheckResult] = []
        for feature, baseline in self.baseline.items():
            actual = self.window.get(feature, [])
            if len(actual) < settings.drift_min_samples:
                continue
            if len(baseline) < settings.drift_min_samples:
                continue

            psi_score = psi(baseline, actual)
            ks_score = ks_statistic(baseline, actual)

            psi_triggered = (
                np.isfinite(psi_score) and psi_score >= settings.drift_psi_threshold
            )
            ks_triggered = (
                np.isfinite(ks_score) and ks_score >= settings.drift_ks_threshold
            )
            severity = "alarm" if (psi_triggered and ks_triggered) else "warn"

            if psi_triggered or ks_triggered:
                results.append(DriftCheckResult(
                    feature=feature,
                    method="psi",
                    score=psi_score,
                    threshold=settings.drift_psi_threshold,
                    triggered=psi_triggered,
                    severity=severity if psi_triggered else "warn",
                ))
                results.append(DriftCheckResult(
                    feature=feature,
                    method="ks",
                    score=ks_score,
                    threshold=settings.drift_ks_threshold,
                    triggered=ks_triggered,
                    severity=severity if ks_triggered else "warn",
                ))
        return results

    def persist(self, results: List[DriftCheckResult]) -> None:
        if not results:
            return
        now_end = datetime.utcnow()
        try:
            with get_session() as s:
                for r in results:
                    if not r.triggered:
                        continue
                    repo.record_drift_event(
                        s,
                        network_id=self.network_id,
                        feature=r.feature,
                        method=r.method,
                        score=float(r.score),
                        threshold=float(r.threshold),
                        severity=r.severity,
                        bundle_id=self.bundle_id,
                        window_end=now_end,
                    )
                    metrics_mod.record_drift_metric(
                        network_id=self.network_id,
                        method=r.method,
                        severity=r.severity,
                    )
        except Exception as e:
            logger.warning(f"[drift] persist failed: {e}")

    def reset_window(self) -> None:
        for k in list(self.window.keys()):
            self.window[k] = []
