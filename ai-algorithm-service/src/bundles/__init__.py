"""Model Bundle runtime infrastructure (READ-ONLY trên service).

Runtime bundle có thể được build bởi CI/CD (sim bundle -> runtime bundle) và
upload qua MinIO → service extract + validate + serve.

Bundle format chuẩn (manifest, checksum, topology_hash) đến từ shared package
`traffic_rl_features.bundle`. Các shim trong package này (manifest.py,
checksum.py, topology_hash.py) chỉ re-export để giữ backward compat với
import path cũ.
"""

from src.bundles.manifest import (
    BUNDLE_FILES_REQUIRED,
    MANIFEST_FILENAME,
    ModelManifest,
)
from src.bundles.checksum import compute_file_sha256, compute_bundle_checksum
from src.bundles.topology_hash import compute_topology_hash
from src.bundles.extractor import (
    BundleValidationError,
    extract_bundle_zip,
    validate_bundle_dir,
)
from src.bundles.active import (
    ActivePointer,
    read_active_pointer,
    write_active_pointer,
)
from src.bundles.storage import (
    archive_dir,
    bundle_root,
    bundle_zip_path,
    bundles_dir,
    models_root,
    network_dir,
    networks_root,
    remote_bundle_uri,
    sim_bundle_root,
    sim_bundle_zip_path,
    sim_bundles_root,
)

__all__ = [
    "ActivePointer",
    "BUNDLE_FILES_REQUIRED",
    "BundleValidationError",
    "MANIFEST_FILENAME",
    "ModelManifest",
    "archive_dir",
    "bundle_root",
    "bundle_zip_path",
    "bundles_dir",
    "compute_bundle_checksum",
    "compute_file_sha256",
    "compute_topology_hash",
    "extract_bundle_zip",
    "models_root",
    "network_dir",
    "networks_root",
    "read_active_pointer",
    "remote_bundle_uri",
    "sim_bundle_root",
    "sim_bundle_zip_path",
    "sim_bundles_root",
    "validate_bundle_dir",
    "write_active_pointer",
]
