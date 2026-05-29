"""Bundle Extractor — giải nén + validate Model Bundle.

Validation gồm 4 lớp (Defense in Depth, Lớp 1 trong cơ chế an toàn):
  1. ZIP structure: file bắt buộc tồn tại.
  2. Manifest schema: parse model_manifest.json.
  3. Topology hash: hash thực tế của network.json == topology_hash trong manifest.
  4. Checksum: file_checksums khớp + bundle checksum khớp.

Thêm validate config-level:
  - observation_mask phải đủ 12 giá trị.
  - phase_mapping value ∈ [-1, 7].
  - tồn tại ít nhất 1 phase hợp lệ trong mỗi cross config.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable, List

from traffic_rl_features import PACKAGE_VERSION as FEATURE_PKG_VERSION
from traffic_rl_features import is_compatible as is_feature_pkg_compatible

from src.bundles.checksum import compute_bundle_checksum, compute_file_sha256
from src.bundles.manifest import (
    BUNDLE_FILES_REQUIRED,
    MANIFEST_FILENAME,
    ModelManifest,
)
from src.bundles.topology_hash import compute_topology_hash


_REQUIRED_MASK_LEN = 12
_PHASE_VALUE_MIN = -1
_PHASE_VALUE_MAX = 7


class BundleValidationError(Exception):
    """Bundle không pass validate. Bundle bị reject, không activate."""


def extract_bundle_zip(
    zip_path: Path,
    target_dir: Path,
) -> Path:
    """Giải nén bundle ZIP vào `target_dir/<bundle_id>/`.

    Trả về root của thư mục đã giải nén. Raise BundleValidationError nếu ZIP
    không hợp lệ (thiếu manifest hoặc file bắt buộc).
    """
    zip_path = Path(zip_path)
    target_dir = Path(target_dir)
    if not zip_path.exists():
        raise BundleValidationError(f"Bundle ZIP không tồn tại: {zip_path}")

    # Đọc manifest trước khi extract để biết bundle_id.
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            for required in BUNDLE_FILES_REQUIRED:
                if required not in names:
                    raise BundleValidationError(
                        f"Bundle ZIP thiếu file bắt buộc: {required}"
                    )
            with zf.open(MANIFEST_FILENAME) as mf:
                manifest_data = json.loads(mf.read().decode("utf-8"))
            manifest = ModelManifest.from_dict(manifest_data)

            extract_root = target_dir / manifest.bundle_id
            extract_root.mkdir(parents=True, exist_ok=True)
            zf.extractall(extract_root)
    except zipfile.BadZipFile as e:
        raise BundleValidationError(f"Bundle ZIP hỏng: {e}") from e

    return extract_root


def validate_bundle_dir(bundle_root: Path) -> ModelManifest:
    """Validate thư mục bundle đã giải nén. Raise BundleValidationError nếu fail.

    Không sửa file. Chỉ đọc + so checksum + parse config.
    """
    bundle_root = Path(bundle_root)

    # 1. File required
    for required in BUNDLE_FILES_REQUIRED:
        if not (bundle_root / required).exists():
            raise BundleValidationError(
                f"Bundle thiếu file bắt buộc: {required}"
            )

    # 2. Parse manifest
    manifest_path = bundle_root / MANIFEST_FILENAME
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = ModelManifest.from_dict(json.load(f))
    except (ValueError, json.JSONDecodeError) as e:
        raise BundleValidationError(f"Manifest không hợp lệ: {e}") from e

    # 3. Topology hash
    actual_topo = compute_topology_hash(bundle_root / "network.json")
    if actual_topo != manifest.topology_hash:
        raise BundleValidationError(
            f"Topology hash mismatch: actual={actual_topo} "
            f"expected={manifest.topology_hash}. "
            f"Cấu trúc đường có thể đã thay đổi."
        )

    # 4. File checksums
    if not manifest.file_checksums:
        raise BundleValidationError("Manifest thiếu file_checksums.")
    for relpath, expected_hex in manifest.file_checksums.items():
        target = bundle_root / relpath
        if not target.exists():
            raise BundleValidationError(
                f"File trong manifest không tồn tại trên đĩa: {relpath}"
            )
        actual_hex = compute_file_sha256(target)
        if actual_hex != expected_hex:
            raise BundleValidationError(
                f"Checksum mismatch cho {relpath}: "
                f"actual={actual_hex[:16]}.. expected={expected_hex[:16]}.."
            )

    aggregate = compute_bundle_checksum(manifest.file_checksums)
    if aggregate != manifest.checksum:
        raise BundleValidationError(
            f"Bundle aggregate checksum mismatch: "
            f"actual={aggregate} expected={manifest.checksum}"
        )

    # 5. Config-level validate (mask, phase_mapping, ≥1 phase hợp lệ)
    _validate_intersection_configs(bundle_root, manifest.intersection_files)

    # 6. V2 commissioning artifacts (nếu manifest có sha256 -> bắt buộc file tồn tại + khớp).
    _validate_v2_commissioning(bundle_root, manifest)

    return manifest


def _validate_v2_commissioning(
    bundle_root: Path, manifest: ModelManifest
) -> None:
    """Verify deployment_map.json + feature_formula.json khớp sha256 trong manifest.

    Chỉ áp dụng nếu manifest có khai báo sha256 (bundle v2). Bundle v1 (legacy)
    có 2 trường này = None → skip silent.
    """
    if manifest.deployment_map_sha256:
        path = bundle_root / "deployment_map.json"
        if not path.exists():
            raise BundleValidationError(
                "Manifest có deployment_map_sha256 nhưng deployment_map.json thiếu."
            )
        actual = compute_file_sha256(path)
        if actual != manifest.deployment_map_sha256:
            raise BundleValidationError(
                f"deployment_map.json checksum mismatch: "
                f"actual={actual[:16]}.. expected={manifest.deployment_map_sha256[:16]}.."
            )

    if manifest.feature_formula_sha256:
        path = bundle_root / "feature_formula.json"
        if not path.exists():
            raise BundleValidationError(
                "Manifest có feature_formula_sha256 nhưng feature_formula.json thiếu."
            )
        actual = compute_file_sha256(path)
        if actual != manifest.feature_formula_sha256:
            raise BundleValidationError(
                f"feature_formula.json checksum mismatch: "
                f"actual={actual[:16]}.. expected={manifest.feature_formula_sha256[:16]}.."
            )

    # Feature package version: major mismatch → fail. Bundle build với traffic_rl_features
    # v2.x, runtime đang chạy v1.x → spec/evaluator có thể không tương thích → reject.
    if manifest.feature_pkg_version:
        if not is_feature_pkg_compatible(manifest.feature_pkg_version, FEATURE_PKG_VERSION):
            raise BundleValidationError(
                f"feature_pkg_version mismatch: bundle={manifest.feature_pkg_version} "
                f"runtime={FEATURE_PKG_VERSION}. Khác MAJOR version — distribution "
                f"observation có thể đã thay đổi không tương thích. Rebuild bundle "
                f"với traffic_rl_features đang chạy hoặc update runtime."
            )


def _validate_intersection_configs(
    bundle_root: Path,
    intersection_files: Iterable[str],
) -> None:
    files: List[str] = list(intersection_files)
    if not files:
        # Không có intersection config — chấp nhận (network có thể chỉ có 1 cross
        # và config rỗng), nhưng cảnh báo qua exception riêng nếu cần thì để
        # caller quyết định.
        return

    for relpath in files:
        path = bundle_root / relpath
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise BundleValidationError(
                f"Cross config không đọc được {relpath}: {e}"
            ) from e

        mask = cfg.get("observation_mask")
        if mask is not None and len(mask) != _REQUIRED_MASK_LEN:
            raise BundleValidationError(
                f"{relpath}: observation_mask phải có {_REQUIRED_MASK_LEN} "
                f"phần tử, got {len(mask)}."
            )

        phase_mapping = cfg.get("phase_mapping")
        if phase_mapping is not None:
            valid_count = 0
            for v in phase_mapping:
                try:
                    iv = int(v)
                except (TypeError, ValueError) as e:
                    raise BundleValidationError(
                        f"{relpath}: phase_mapping chứa giá trị không phải int."
                    ) from e
                if iv < _PHASE_VALUE_MIN or iv > _PHASE_VALUE_MAX:
                    raise BundleValidationError(
                        f"{relpath}: phase_mapping value={iv} ngoài "
                        f"[{_PHASE_VALUE_MIN}, {_PHASE_VALUE_MAX}]."
                    )
                if iv >= 0:
                    valid_count += 1
            if valid_count < 1:
                raise BundleValidationError(
                    f"{relpath}: phase_mapping không có phase hợp lệ nào."
                )
