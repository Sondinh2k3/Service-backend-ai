"""Sim bundle utilities (training outputs only).

Sim bundle chứa artifacts từ training (không có real_normalization):
  - policy.onnx
  - policy_meta.json
  - sim_network.json (sim normalization / sim network contract)
  - sim_bundle_manifest.json (metadata)

Backward compatibility: bundle cũ có `intersection_config.json` vẫn được đọc,
nhưng bundle mới nên dùng tên `sim_network.json` để phân định rõ sim-side
normalization với runtime `network.json`.

Sim bundle được dùng làm input cho composer để build runtime bundle.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional


SIM_BUNDLE_MANIFEST_FILENAME = "sim_bundle_manifest.json"
SIM_NETWORK_FILENAME = "sim_network.json"
LEGACY_SIM_CONFIG_FILENAME = "intersection_config.json"
SIM_BUNDLE_FILES_REQUIRED = (
    SIM_BUNDLE_MANIFEST_FILENAME,
    "policy.onnx",
    "policy_meta.json",
)

# Schema versions phia service hieu duoc. Khi bump len phien ban moi, them vao
# tap nay; sim bundle ngoai tap se bi reject voi loi ro rang thay vi crash o
# buoc sau.
SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS = frozenset({1})
CURRENT_SIM_BUNDLE_SCHEMA_VERSION = 1


class SimBundleValidationError(Exception):
    """Sim bundle không hợp lệ."""


@dataclass
class SimBundleManifest:
    sim_bundle_id: str
    tenant_id: str
    network_id: str
    version: str
    sim_network_path: str = SIM_NETWORK_FILENAME
    policy_onnx_path: str = "policy.onnx"
    policy_meta_path: str = "policy_meta.json"
    training_run_id: Optional[str] = None
    training_dataset_id: Optional[str] = None
    training_pipeline_commit: Optional[str] = None
    created_at: Optional[str] = None
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Dict) -> "SimBundleManifest":
        try:
            return cls(
                sim_bundle_id=str(data["sim_bundle_id"]),
                tenant_id=str(data.get("tenant_id", "default")),
                network_id=str(data["network_id"]),
                version=str(data["version"]),
                sim_network_path=str(
                    data.get("sim_network_path")
                    or data.get("sim_config_path")
                    or LEGACY_SIM_CONFIG_FILENAME
                ),
                policy_onnx_path=str(data.get("policy_onnx_path", "policy.onnx")),
                policy_meta_path=str(data.get("policy_meta_path", "policy_meta.json")),
                training_run_id=data.get("training_run_id"),
                training_dataset_id=data.get("training_dataset_id"),
                training_pipeline_commit=data.get("training_pipeline_commit"),
                created_at=data.get("created_at"),
                schema_version=int(data.get("schema_version", 1)),
            )
        except KeyError as e:
            raise SimBundleValidationError(f"Sim bundle manifest thiếu field: {e}") from e

    def to_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "sim_bundle_id": self.sim_bundle_id,
            "tenant_id": self.tenant_id,
            "network_id": self.network_id,
            "version": self.version,
            "sim_network_path": self.sim_network_path,
            # Keep old key for tools that still call this sim_config.
            "sim_config_path": self.sim_network_path,
            "policy_onnx_path": self.policy_onnx_path,
            "policy_meta_path": self.policy_meta_path,
            "training_run_id": self.training_run_id,
            "training_dataset_id": self.training_dataset_id,
            "training_pipeline_commit": self.training_pipeline_commit,
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
        }


def is_sim_bundle_zip(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            return SIM_BUNDLE_MANIFEST_FILENAME in names
    except zipfile.BadZipFile:
        return False


def extract_sim_bundle_zip(zip_path: Path, target_dir: Path) -> Path:
    zip_path = Path(zip_path)
    target_dir = Path(target_dir)
    if not zip_path.exists():
        raise SimBundleValidationError(f"Sim bundle ZIP không tồn tại: {zip_path}")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            for required in SIM_BUNDLE_FILES_REQUIRED:
                if required not in names:
                    raise SimBundleValidationError(
                        f"Sim bundle ZIP thiếu file bắt buộc: {required}"
                    )
            with zf.open(SIM_BUNDLE_MANIFEST_FILENAME) as mf:
                manifest_data = json.loads(mf.read().decode("utf-8"))
            manifest = SimBundleManifest.from_dict(manifest_data)
            if manifest.sim_network_path not in names:
                raise SimBundleValidationError(
                    f"Sim bundle ZIP thiếu file sim network: {manifest.sim_network_path}"
                )

            extract_root = target_dir / manifest.sim_bundle_id
            extract_root.mkdir(parents=True, exist_ok=True)
            zf.extractall(extract_root)
    except zipfile.BadZipFile as e:
        raise SimBundleValidationError(f"Sim bundle ZIP hỏng: {e}") from e

    return extract_root


def validate_sim_bundle_dir(bundle_root: Path) -> SimBundleManifest:
    bundle_root = Path(bundle_root)
    for required in SIM_BUNDLE_FILES_REQUIRED:
        if not (bundle_root / required).exists():
            raise SimBundleValidationError(
                f"Sim bundle thiếu file bắt buộc: {required}"
            )
    manifest_path = bundle_root / SIM_BUNDLE_MANIFEST_FILENAME
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = SimBundleManifest.from_dict(json.load(f))
    except (ValueError, json.JSONDecodeError) as e:
        raise SimBundleValidationError(f"Sim bundle manifest không hợp lệ: {e}") from e

    _check_schema_version(manifest)
    _validate_manifest_paths(bundle_root, manifest)
    return manifest


def _check_schema_version(manifest: SimBundleManifest) -> None:
    """Reject sim bundle co schema_version khong nam trong tap ho tro.

    Tach phan nay ra de doi xu rieng (loi cau truc, khong phai loi tam thoi nhu
    thieu real snapshot).
    """
    if manifest.schema_version not in SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS:
        raise SimBundleValidationError(
            f"Sim bundle schema_version={manifest.schema_version} khong duoc "
            f"ho tro. Service hieu cac phien ban: "
            f"{sorted(SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS)}. "
            f"Hay rebuild sim bundle voi schema_version="
            f"{CURRENT_SIM_BUNDLE_SCHEMA_VERSION} hoac upgrade service."
        )


def _validate_manifest_paths(bundle_root: Path, manifest: SimBundleManifest) -> None:
    required_paths: Iterable[str] = (
        manifest.sim_network_path,
        manifest.policy_onnx_path,
        manifest.policy_meta_path,
    )
    for rel in required_paths:
        if not (bundle_root / rel).exists():
            raise SimBundleValidationError(
                f"Sim bundle thiếu file {rel} (tham chiếu trong manifest)"
            )
