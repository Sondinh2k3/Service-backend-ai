"""Anti-starvation tracker.

Theo doi so lan lien tiep moi (cross_id, stage_idx) bi gan green-time = min_green.
Neu vuot nguong, guardrails se boost up green-time cho stage do len recovery_green
de tranh hien tuong "huong bi bo quen".

Tracker in-process — KHONG persistent. Dac biet voi MVP single-instance ai-runtime.
Khi scale ngang, can chuyen sang Redis hoac shared cache.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, Optional, Tuple


_KeyType = Tuple[int, int]  # (cross_id, stage_idx)


class StarvationTracker:
    def __init__(self) -> None:
        self._counts: Dict[_KeyType, int] = defaultdict(int)
        self._lock = threading.Lock()

    def record_min_green(self, cross_id: int, stage_idx: int) -> int:
        """Tang counter, tra ve so lan bi pin tai min."""
        key = (cross_id, stage_idx)
        with self._lock:
            self._counts[key] += 1
            return self._counts[key]

    def reset(self, cross_id: int, stage_idx: int) -> None:
        key = (cross_id, stage_idx)
        with self._lock:
            self._counts.pop(key, None)

    def reset_cross(self, cross_id: int) -> None:
        with self._lock:
            keys = [k for k in self._counts if k[0] == cross_id]
            for k in keys:
                self._counts.pop(k, None)

    def count(self, cross_id: int, stage_idx: int) -> int:
        return self._counts.get((cross_id, stage_idx), 0)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {f"{c}:{s}": v for (c, s), v in self._counts.items()}


_GLOBAL: Optional[StarvationTracker] = None
_GLOBAL_LOCK = threading.Lock()


def get_starvation_tracker() -> StarvationTracker:
    global _GLOBAL
    if _GLOBAL is None:
        with _GLOBAL_LOCK:
            if _GLOBAL is None:
                _GLOBAL = StarvationTracker()
    return _GLOBAL
