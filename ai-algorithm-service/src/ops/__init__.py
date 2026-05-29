"""ai-ops (Container 2.1) — Model Operations.

Trach nhiem:
  - Pull Model Bundle tu Artifact Store (MinIO/S3) ve Local Model Storage.
  - Validate Bundle (checksum, manifest, topology hash, config-level).
  - Cross-validate (network_id, topo_hash) voi area da dang ky.
  - Activate / Rollback bundle.
  - Audit moi thao tac qua bundle_event.

Khong serve inference. Khong dung onnxruntime.
"""

from src.ops.lifecycle import (
    BundleLifecycleError,
    activate_bundle,
    pull_and_register_bundle,
    register_local_bundle,
    rollback_bundle,
)

__all__ = [
    "BundleLifecycleError",
    "activate_bundle",
    "pull_and_register_bundle",
    "register_local_bundle",
    "rollback_bundle",
]
