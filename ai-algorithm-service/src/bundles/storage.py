"""Local Model Storage layout helpers.

Layout (khi `bundle_layout_enabled=True`):

  <model_dir>/
    networks/
      <network_id>/
        active.json                          # ActivePointer
        bundles/
          <bundle_id>/                       # bundle giai nen
            policy.onnx
            policy_meta.json
            network.json
            intersections/cross_<id>.json
            model_manifest.json
        archive/<bundle_id>.zip              # zip da pull (cache)

Layout legacy (backward compat khi chua co bundle nao active):

  <model_dir>/
    area_<area_id>/
      policy.onnx
      policy_meta.json
      ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core.config import get_settings


def models_root() -> Path:
    settings = get_settings()
    p = Path(settings.model_dir)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def networks_root() -> Path:
    return models_root() / "networks"


def network_dir(network_id: str) -> Path:
    return networks_root() / network_id


def bundles_dir(network_id: str) -> Path:
    return network_dir(network_id) / "bundles"


def archive_dir(network_id: str) -> Path:
    return network_dir(network_id) / "archive"


def bundle_root(network_id: str, bundle_id: str) -> Path:
    return bundles_dir(network_id) / bundle_id


def bundle_zip_path(network_id: str, bundle_id: str) -> Path:
    return archive_dir(network_id) / f"{bundle_id}.zip"


def sim_bundles_root() -> Path:
    return models_root() / "sim_bundles"


def sim_bundle_root(sim_bundle_id: str) -> Path:
    return sim_bundles_root() / sim_bundle_id


def sim_bundle_zip_path(sim_bundle_id: str) -> Path:
    return sim_bundles_root() / "archive" / f"{sim_bundle_id}.zip"


def remote_bundle_uri(
    *, tenant_id: str, network_id: str, version: str, bundle_id: Optional[str] = None
) -> str:
    """Compute s3:// URI cho bundle ZIP tren Artifact Store.

    Pattern: s3://{bucket}/{prefix}/bundles/{tenant}/{network}/{version}/bundle.zip
    Hoac fallback ve filename per-bundle neu bundle_id duoc cung cap.
    """
    settings = get_settings()
    bucket = settings.minio_bucket or "ai-models"
    prefix_parts: list[str] = []
    if settings.minio_prefix:
        prefix_parts.append(settings.minio_prefix.strip("/"))
    if settings.artifact_bundle_prefix:
        prefix_parts.append(settings.artifact_bundle_prefix.strip("/"))
    prefix_parts.extend([tenant_id, network_id, version])
    filename = f"{bundle_id}.zip" if bundle_id else "bundle.zip"
    key = "/".join(part for part in prefix_parts if part) + "/" + filename
    return f"s3://{bucket}/{key}"
