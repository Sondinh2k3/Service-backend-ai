"""Preflight check (Strict Mode) — fail-fast khi swap bundle.

Chay khi:
  - Process startup (kiem tra moi network co active bundle).
  - Phat hien active.json doi -> truoc khi reload.

Logic:
  1. Doc active.json. Neu khong co -> raise PreflightError.
  2. Doc bundle root tu pointer. Validate cac file bat buoc.
  3. Chay validate_bundle_dir (manifest, checksum, topology, configs).
  4. So topology_hash trong pointer voi manifest.
  5. So with cu thuc te (tinh lai network.json hash) — phong truong hop file da bi
     thay doi sau khi pull.

Khong load ONNX o day — runtime engine se load qua model_manager khi can.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.bundles import (
    ActivePointer,
    BundleValidationError,
    ModelManifest,
    bundle_root,
    network_dir,
    read_active_pointer,
    validate_bundle_dir,
)
from src.bundles.topology_hash import compute_topology_hash
from src.core.logger import logger


class PreflightError(Exception):
    """Bundle Active khong san sang serve. Runtime KHONG nen serve voi model nay."""


def run_preflight(network_id: str) -> tuple[ActivePointer, ModelManifest]:
    """Validate bundle Active cua 1 network. Raise PreflightError neu fail."""
    pointer = read_active_pointer(network_dir(network_id))
    if pointer is None:
        raise PreflightError(f"Network {network_id} chua co active.json.")

    bundle_path = bundle_root(network_id, pointer.bundle_id)
    if not bundle_path.exists():
        raise PreflightError(
            f"Bundle dir khong ton tai: {bundle_path}"
        )

    try:
        manifest = validate_bundle_dir(bundle_path)
    except BundleValidationError as e:
        raise PreflightError(f"Bundle validate fail: {e}") from e

    if pointer.topology_hash and pointer.topology_hash != manifest.topology_hash:
        raise PreflightError(
            f"Active pointer topo_hash != manifest topo_hash: "
            f"{pointer.topology_hash[:12]}.. vs {manifest.topology_hash[:12]}.."
        )

    actual_hash = compute_topology_hash(bundle_path / "network.json")
    if actual_hash != manifest.topology_hash:
        raise PreflightError(
            f"network.json bi sua sau khi pull: actual={actual_hash[:12]}.. "
            f"expected={manifest.topology_hash[:12]}.."
        )

    logger.info(
        f"[runtime] Preflight ok network={network_id} bundle={pointer.bundle_id} "
        f"version={pointer.version}"
    )
    return pointer, manifest
