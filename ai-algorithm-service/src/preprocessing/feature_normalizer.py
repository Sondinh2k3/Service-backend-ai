"""
Feature normalization: áp obs_stats (mean/std) đã export từ training lên observation.

Nếu config không có obs_stats, feature đã được scale vào [0, 1] ở bước topology
normalization nên bỏ qua bước này. Nếu có, áp z-score:
    normalized = clip((x - mean) / std, -clip, clip)
"""

from __future__ import annotations

from typing import Optional

import numpy as np


class FeatureNormalizer:
    """Apply z-score normalization dùng stats export từ training."""

    def __init__(
        self,
        mean: Optional[np.ndarray] = None,
        std: Optional[np.ndarray] = None,
        clip: float = 10.0,
        epsilon: float = 1e-8,
    ):
        self.mean = np.asarray(mean, dtype=np.float32) if mean is not None else None
        self.std = np.asarray(std, dtype=np.float32) if std is not None else None
        self.clip = clip
        self.epsilon = epsilon

    @property
    def enabled(self) -> bool:
        return self.mean is not None and self.std is not None

    def apply(self, obs: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return obs.astype(np.float32, copy=False)

        if self.mean.shape != obs.shape or self.std.shape != obs.shape:
            # Shape mismatch -> bỏ qua để tránh crash prod
            return obs.astype(np.float32, copy=False)

        normalized = (obs.astype(np.float32) - self.mean) / (self.std + self.epsilon)
        return np.clip(normalized, -self.clip, self.clip).astype(np.float32)
