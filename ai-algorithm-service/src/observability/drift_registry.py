"""Singleton registry: 1 DriftDetector per network_id.

Auto-baseline policy:
  - Khi observe lan dau cho 1 feature, samples duoc tich vao temp warmup buffer.
  - Khi buffer du `drift_min_samples`, chuyen sang baseline va clear buffer.
  - Sau do moi observe vao window thuc su.

Counter-based check trigger:
  - record_observation() inc counter; moi `drift_check_interval` lan, run check()
    + persist + clear window.

Reset:
  - reset_detector(network_id) goi khi bundle activate moi (baseline co the doi).
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from src.core.config import get_settings
from src.core.logger import logger
from src.observability.drift import DriftCheckResult, DriftDetector


_detectors: Dict[str, DriftDetector] = {}
_warmup_buffers: Dict[str, Dict[str, List[float]]] = {}  # network_id -> feature -> samples
_counters: Dict[str, int] = {}  # network_id -> inference count since last check
_lock = threading.Lock()


def get_detector(network_id: str, *, bundle_id: Optional[str] = None) -> DriftDetector:
    with _lock:
        det = _detectors.get(network_id)
        if det is None:
            det = DriftDetector(network_id=network_id, bundle_id=bundle_id)
            _detectors[network_id] = det
            _warmup_buffers[network_id] = {}
            _counters[network_id] = 0
        elif bundle_id and det.bundle_id != bundle_id:
            det.bundle_id = bundle_id
        return det


def record_observation(
    network_id: str,
    feature: str,
    value: float,
    *,
    bundle_id: Optional[str] = None,
) -> None:
    """Tich vao baseline (warmup) hoac window (operational). Increase counter."""
    settings = get_settings()
    if not settings.drift_enabled:
        return

    det = get_detector(network_id, bundle_id=bundle_id)

    with _lock:
        if feature not in det.baseline:
            buf = _warmup_buffers.setdefault(network_id, {}).setdefault(feature, [])
            buf.append(float(value))
            if len(buf) >= settings.drift_min_samples:
                det.set_baseline(feature, buf)
                _warmup_buffers[network_id][feature] = []
                logger.info(
                    f"[drift] Baseline ready for network={network_id} feature={feature} "
                    f"({len(buf)} samples)"
                )
        else:
            window = det.window.setdefault(feature, [])
            window.append(float(value))
            if (
                settings.drift_window_size > 0
                and len(window) > settings.drift_window_size
            ):
                # Bo sample cu nhat (trim head).
                drop = len(window) - settings.drift_window_size
                del window[:drop]


def maybe_check(network_id: str) -> List[DriftCheckResult]:
    """Increment counter; moi `drift_check_interval` lan, chay check + persist."""
    settings = get_settings()
    if not settings.drift_enabled:
        return []

    with _lock:
        cnt = _counters.get(network_id, 0) + 1
        _counters[network_id] = cnt
        if cnt < settings.drift_check_interval:
            return []
        _counters[network_id] = 0
        det = _detectors.get(network_id)

    if det is None:
        return []

    results = det.check()
    if results:
        triggered = [r for r in results if r.triggered]
        if triggered:
            det.persist(results)
            logger.warning(
                f"[drift] Detected on network={network_id}: "
                f"{[(r.feature, r.method, round(r.score, 3), r.severity) for r in triggered]}"
            )
        # Clear window de bat dau cua so moi (drift detector pattern).
        det.reset_window()
    return results


def reset_detector(network_id: str) -> None:
    """Goi khi bundle activate moi — baseline co the can xay lai voi obs_stats moi."""
    with _lock:
        _detectors.pop(network_id, None)
        _warmup_buffers.pop(network_id, None)
        _counters.pop(network_id, None)
    logger.info(f"[drift] Reset detector for network={network_id}")


def reset_all() -> None:
    with _lock:
        _detectors.clear()
        _warmup_buffers.clear()
        _counters.clear()


def snapshot() -> Dict[str, dict]:
    """Debug helper: trang thai cua tat ca detector."""
    with _lock:
        out: Dict[str, dict] = {}
        for nid, det in _detectors.items():
            out[nid] = {
                "bundle_id": det.bundle_id,
                "baseline_features": list(det.baseline.keys()),
                "baseline_sizes": {f: len(s) for f, s in det.baseline.items()},
                "window_sizes": {f: len(s) for f, s in det.window.items()},
                "warmup_sizes": {
                    f: len(s) for f, s in _warmup_buffers.get(nid, {}).items()
                },
                "counter": _counters.get(nid, 0),
            }
        return out
