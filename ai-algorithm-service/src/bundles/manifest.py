"""Backward-compat shim: re-export ModelManifest từ shared package.

Bundle manifest schema được chuyển sang `traffic_rl_features.bundle.manifest`
để cả bundle-tooling (build-time) và ai-algorithm-service (runtime) cùng import
một định nghĩa duy nhất, tránh drift schema.

Code mới khuyến nghị import trực tiếp `from traffic_rl_features.bundle import ...`.
"""

from __future__ import annotations

from traffic_rl_features.bundle.manifest import (
    BUNDLE_FILES_REQUIRED,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ModelManifest,
)

__all__ = [
    "BUNDLE_FILES_REQUIRED",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "ModelManifest",
]
