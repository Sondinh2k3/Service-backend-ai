"""Test extractor validate commissioning artifacts.

Service không build bundle — chỉ extract. Bundle để test được build qua
`bundle_tooling` package riêng (dev dependency, không vào Docker image).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bundles.extractor import (
    BundleValidationError,
    extract_bundle_zip,
    validate_bundle_dir,
)


_EXAMPLES = Path(__file__).parent.parent.parent / "bundle-tooling" / "examples" / "cologne3"
_SIM_CONFIG_PATH = _EXAMPLES / "intersection_config.json"
_DEPLOY_EXAMPLE_PATH = _EXAMPLES / "deployment_map.example.json"


def _build_v2_bundle(tmp_path: Path) -> Path:
    """Dùng bundle_tooling (dev dep) build bundle test."""
    pytest.importorskip("bundle_tooling")
    from bundle_tooling import build_v2_bundle_zip

    onnx = tmp_path / "policy.onnx"
    onnx.write_bytes(b"\x00FAKE_ONNX")
    meta = tmp_path / "policy_meta.json"
    meta.write_text(json.dumps({
        "obs_dim": 336, "base_obs_dim": 56, "window_size": 6,
        "num_actions_per_phase": 5, "keep_action_index": 2,
        "input_names": ["self_features"], "use_local_gnn": True,
    }))
    out_zip = tmp_path / "bundle.zip"
    build_v2_bundle_zip(
        sim_config_path=_SIM_CONFIG_PATH,
        deployment_map_path=_DEPLOY_EXAMPLE_PATH,
        policy_onnx_path=onnx,
        policy_meta_path=meta,
        output_zip=out_zip,
        tenant_id="test_tenant",
        version="v0.0.1",
    )
    return out_zip


@pytest.mark.skipif(
    not _SIM_CONFIG_PATH.exists() or not _DEPLOY_EXAMPLE_PATH.exists(),
    reason="thiếu bundle-tooling example fixtures",
)
def test_v2_bundle_extract_validate_roundtrip(tmp_path: Path):
    zip_path = _build_v2_bundle(tmp_path)
    extract_root = extract_bundle_zip(zip_path, tmp_path / "extracted")
    manifest = validate_bundle_dir(extract_root)

    assert manifest.sim_network_id == "cologne3"
    assert manifest.deployment_map_sha256 is not None
    assert manifest.feature_formula_sha256 is not None


@pytest.mark.skipif(
    not _SIM_CONFIG_PATH.exists() or not _DEPLOY_EXAMPLE_PATH.exists(),
    reason="thiếu fixtures",
)
def test_v2_bundle_detects_tampered_deployment_map(tmp_path: Path):
    zip_path = _build_v2_bundle(tmp_path)
    extract_root = extract_bundle_zip(zip_path, tmp_path / "extracted")

    dm_path = extract_root / "deployment_map.json"
    content = dm_path.read_text(encoding="utf-8")
    dm_path.write_text(content + " ", encoding="utf-8")

    with pytest.raises(BundleValidationError, match="checksum mismatch|deployment_map"):
        validate_bundle_dir(extract_root)


@pytest.mark.skipif(
    not _SIM_CONFIG_PATH.exists() or not _DEPLOY_EXAMPLE_PATH.exists(),
    reason="thiếu fixtures",
)
def test_v2_bundle_detects_tampered_feature_formula(tmp_path: Path):
    zip_path = _build_v2_bundle(tmp_path)
    extract_root = extract_bundle_zip(zip_path, tmp_path / "extracted")

    ff_path = extract_root / "feature_formula.json"
    ff_path.write_text(json.dumps({
        "channels": ["density", "queue", "occupancy", "speed"],
        "formulas": {
            "density": "occupancy / 50.0",
            "queue": "occupancy / 100.0",
            "occupancy": "occupancy / 100.0",
            "speed": "speed / 50.0",
        },
    }), encoding="utf-8")

    with pytest.raises(BundleValidationError, match="checksum mismatch|feature_formula"):
        validate_bundle_dir(extract_root)
