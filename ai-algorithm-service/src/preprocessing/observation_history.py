"""
Observation history buffer per (area_id, cross_id) cho policy dùng window history.

Training pipeline có thể stack `window_size` timestep gần nhất thành tensor
(B, T, base_obs_dim) trước khi đưa vào policy. Runtime nhận observation tại 1
timestep → cần buffer giữ lại history qua nhiều request inference của cùng
(area_id, cross_id) thì mới feed đúng cái policy đã train.

Cold start (lần đầu thấy cross): replicate observation hiện tại × window_size.

Thread-safe; mỗi entry là deque maxlen=window_size. Không persist xuống disk —
nếu service restart, buffer cold-start lại. Đây là tradeoff đơn giản hóa; nếu
cần survive restart hoặc scale-out nhiều instance, đẩy storage qua Redis với
cùng API.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, Tuple

import numpy as np


class ObservationHistory:
    """Buffer history observation per (area_id, cross_id)."""

    def __init__(self) -> None:
        self._buffers: Dict[Tuple[int, int], Deque[np.ndarray]] = {}
        self._lock = threading.Lock()

    def push_and_get_window(
        self,
        area_id: int,
        cross_id: int,
        obs_t: np.ndarray,
        window_size: int,
        base_obs_dim: int,
    ) -> np.ndarray:
        """Push observation mới và trả về cửa sổ history shape (window_size, base_obs_dim).

        - Cold start: tạo buffer mới, fill window_size lần obs_t.
        - Hot path: push obs_t, deque tự drop entry cũ nhất.
        """
        if obs_t.shape != (base_obs_dim,):
            raise ValueError(
                f"obs_t shape {obs_t.shape} != expected ({base_obs_dim},)"
            )

        key = (int(area_id), int(cross_id))
        with self._lock:
            buf = self._buffers.get(key)
            if buf is None or buf.maxlen != window_size:
                # Cold start hoặc window_size đã đổi (bundle mới khác config).
                buf = deque([obs_t.copy() for _ in range(window_size)], maxlen=window_size)
                self._buffers[key] = buf
            else:
                buf.append(obs_t.copy())
            window = np.stack(list(buf), axis=0)  # (T, base_dim)
        return window.astype(np.float32, copy=False)

    def clear(self, area_id: int | None = None) -> None:
        """Xóa buffer. Gọi khi bundle activate mới (window_size đổi) hoặc test."""
        with self._lock:
            if area_id is None:
                self._buffers.clear()
                return
            for k in list(self._buffers.keys()):
                if k[0] == int(area_id):
                    self._buffers.pop(k, None)


_singleton = ObservationHistory()


def get_observation_history() -> ObservationHistory:
    """Singleton instance dùng chung trong process."""
    return _singleton
