"""Tests Bước 3: runtime nhận bundle v2 với cycle/stage-id mapping + feature formula."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from traffic_rl_features import FeatureBuilder, FeatureSpec

from src.preprocessing.feature_builder import (
    build_from_bundle,
    clear_cache,
    get_default_builder,
)
from src.core.config import reset_settings_cache
from src.preprocessing.intersection_registry import (
    IntersectionConfig,
    clear_cache as clear_registry_cache,
    get_config,
)
from src.preprocessing.phase_normalizer import (
    NUM_STANDARD_PHASES,
    _effective_phase_mapping,
    build_action_mask,
)
from src.preprocessing.topology_normalizer import (
    LANES_PER_DIRECTION,
    NUM_DIRECTIONS,
    TOTAL_LANES,
    build_lane_features,
)
from src.schemas.common_schemas.cross import Cross
from src.schemas.common_schemas.cycle import Cycle
from src.schemas.common_schemas.road import Road
from src.schemas.common_schemas.stage_input import StageInput


# ----------------------- Helpers -----------------------------------------


def _make_cross(
    cross_id: int = 567001,
    cycle_id: int = 700001,
    stage_ids: tuple[int, ...] = (800001, 800002),
) -> Cross:
    return Cross(
        id=cross_id,
        areaId=1,
        x=0.0,
        y=0.0,
        cycle=Cycle(
            id=cycle_id, createdDate="2026-01-01", crossName="X", cycleLength=90
        ),
        stages=[
            StageInput(
                id=sid, stageCode=f"P{i}", oldId=f"s{sid}",
                yellow=3, redClear=1, duration=42,
            )
            for i, sid in enumerate(stage_ids)
        ],
        roads=[
            Road(id=100001, direction=1, saturationFlow=1800,
                 averageSpeed=30.0, occupancySpace=50.0),
            Road(id=100002, direction=2, saturationFlow=3600,
                 averageSpeed=25.0, occupancySpace=70.0, toCrossId=567002),
            Road(id=100003, direction=4, saturationFlow=3600,
                 averageSpeed=20.0, occupancySpace=80.0),
        ],
    )


def _make_v2_config(stage_to_std: dict[int, int] | None = None) -> IntersectionConfig:
    stage_to_std = stage_to_std or {800001: 1, 800002: 4}
    std_to_stage = {str(v): k for k, v in stage_to_std.items()}
    cycles = {
        "700001": {
            "is_primary": True,
            "stage_to_standard_phase": {str(k): v for k, v in stage_to_std.items()},
            "standard_phase_to_stage": std_to_stage,
            "num_stages": len(stage_to_std),
        }
    }
    return IntersectionConfig(
        cross_id=567001,
        direction_map={"100001": 0, "100002": 1, "100003": 3},
        observation_mask=[1, 0, 0,  1, 1, 0,  0, 0, 0,  1, 1, 0],
        primary_cycle_id=700001,
        cycles=cycles,
        roads_static={
            "100001": {"lanes": 1, "length_meters": 100, "speed_design_kmh": 50, "saturation_flow": 1800},
            "100002": {"lanes": 2, "length_meters": 150, "speed_design_kmh": 50, "saturation_flow": 3600},
            "100003": {"lanes": 2, "length_meters": 200, "speed_design_kmh": 50, "saturation_flow": 3600},
        },
    )


# ----------------------- Step 3.5: phase_normalizer v2 -------------------


def test_phase_mapping_v2_uses_stage_id():
    """V2 mapping: stage_id 800001 -> std 1, 800002 -> std 4. KHÔNG dùng index."""
    cross = _make_cross(stage_ids=(800002, 800001))  # đảo thứ tự stage!
    cfg = _make_v2_config()
    mapping = _effective_phase_mapping(cross, cfg)
    # Stage 800002 (index 0) -> std 4; stage 800001 (index 1) -> std 1.
    # Nếu dùng index sai sẽ ra [1, 4]. V2 đúng phải ra [4, 1].
    assert mapping == [4, 1]


def test_phase_mapping_v2_fallback_to_primary_when_cycle_unknown():
    """Request có cycle_id khác bundled, fallback sang primary_cycle_id."""
    cross = _make_cross(cycle_id=999999)  # cycle không có trong bundle
    cfg = _make_v2_config()
    mapping = _effective_phase_mapping(cross, cfg)
    # Vẫn dùng cycle 700001 primary
    assert mapping == [1, 4]


def test_phase_mapping_v2_stage_not_in_mapping_returns_minus_one():
    """Stage_id không có trong cycle mapping -> -1 (masked)."""
    cross = _make_cross(stage_ids=(800001, 999))  # 999 không có trong mapping
    cfg = _make_v2_config()
    mapping = _effective_phase_mapping(cross, cfg)
    assert mapping == [1, -1]


def test_phase_mapping_v1_legacy_still_works():
    """Khi không có cycles dict, fallback v1 phase_mapping theo index."""
    cross = _make_cross()
    cfg = IntersectionConfig(
        cross_id=567001,
        direction_map={"100001": 0},
        phase_mapping=[2, 5],  # v1: theo thứ tự stage
    )
    mapping = _effective_phase_mapping(cross, cfg)
    assert mapping == [2, 5]


def test_action_mask_v2():
    cross = _make_cross()
    cfg = _make_v2_config()
    mask = build_action_mask(cross, cfg)
    expected = np.zeros(NUM_STANDARD_PHASES)
    expected[1] = 1.0
    expected[4] = 1.0
    np.testing.assert_array_equal(mask, expected)


# ----------------------- Step 3.6: topology_normalizer + FeatureBuilder ----


def test_build_lane_features_uses_feature_builder():
    cross = _make_cross()
    cfg = _make_v2_config()

    # Custom formula trả về (lanes, speed_design, length, saturation_flow)
    # cho dễ verify mapping.
    builder = FeatureBuilder(
        spec=FeatureSpec(
            channels=("c0_lanes", "c1_sd", "c2_len", "c3_sat"),
            formulas={
                "c0_lanes": "lanes",
                "c1_sd": "speed_design",
                "c2_len": "length",
                "c3_sat": "saturation_flow",
            },
        ),
        roads_static={
            "100001": {"lanes": 1, "length_meters": 100, "speed_design_kmh": 50, "saturation_flow": 1800},
            "100002": {"lanes": 2, "length_meters": 150, "speed_design_kmh": 60, "saturation_flow": 3600},
            "100003": {"lanes": 2, "length_meters": 200, "speed_design_kmh": 70, "saturation_flow": 3600},
        },
    )

    feats, lane_mask = build_lane_features(cross, cfg, feature_builder=builder)
    assert feats.shape == (4, TOTAL_LANES)
    # cfg.observation_mask đè onto lane_mask
    assert lane_mask.tolist() == cfg.observation_mask

    # Direction N (idx 0, lane 0): road 100001 -> lanes=1, length=100, speed_design=50
    assert feats[0, 0] == 1
    assert feats[1, 0] == 50
    assert feats[2, 0] == 100
    # Direction E (idx 1, lane 0): road 100002 -> length=150, speed_design=60
    assert feats[0, 3] == 2
    assert feats[2, 3] == 150
    assert feats[1, 3] == 60


def test_build_lane_features_default_builder_fallback():
    """Không truyền builder -> dùng default formula, không crash."""
    cross = _make_cross()
    cfg = _make_v2_config()
    feats, lane_mask = build_lane_features(cross, cfg)  # no feature_builder
    # Default formula có 4 channel
    assert feats.shape == (4, TOTAL_LANES)


# ----------------------- FeatureBuilder unit ------------------------------


def test_feature_builder_compute_uses_static_lookup():
    builder = FeatureBuilder(
        spec=FeatureSpec(
            channels=("density", "speed_norm"),
            formulas={
                "density": "occupancy / 100.0",
                "speed_norm": "speed / max(speed_design, 1)",
            },
        ),
        roads_static={"123": {"lanes": 2, "length_meters": 100, "speed_design_kmh": 60, "saturation_flow": 1800}},
    )
    out = builder.compute(real_road_id=123, occupancy=50.0, speed=30.0)
    assert out.shape == (2,)
    assert out[0] == pytest.approx(0.5)
    assert out[1] == pytest.approx(30.0 / 60.0)


def test_feature_builder_missing_road_uses_defaults():
    """Road không có trong static -> default 1 lane, length 100, speed_design 50."""
    builder = FeatureBuilder(
        spec=FeatureSpec(
            channels=("x",),
            formulas={"x": "lanes * length / speed_design"},
        ),
        roads_static={},
    )
    out = builder.compute(real_road_id=999, occupancy=0, speed=0)
    assert out[0] == pytest.approx(1 * 100 / 50)


# ----------------------- build_from_bundle + cache ------------------------


def test_build_from_bundle_loads_feature_formula(tmp_path: Path):
    clear_cache()  # ensure fresh
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    ff = {
        "channels": ["density", "speed_norm"],
        "formulas": {
            "density": "occupancy / 100.0",
            "speed_norm": "speed / 50.0",
        },
    }
    (bundle_root / "feature_formula.json").write_text(json.dumps(ff))

    cross_configs = {
        567001: {
            "cross_id": 567001,
            "roads_static": {
                "100001": {"lanes": 1, "length_meters": 100, "speed_design_kmh": 50, "saturation_flow": 1800},
            },
        }
    }
    builder = build_from_bundle(bundle_root, cross_configs, cache_key=("test", 0))
    assert builder.channels == ("density", "speed_norm")
    out = builder.compute(real_road_id=100001, occupancy=50.0, speed=25.0)
    assert out[0] == pytest.approx(0.5)
    assert out[1] == pytest.approx(0.5)

    # Cache hit returns same instance
    builder2 = build_from_bundle(bundle_root, cross_configs, cache_key=("test", 0))
    assert builder is builder2

    clear_cache()


def test_build_from_bundle_missing_feature_formula_uses_default(tmp_path: Path):
    clear_cache()
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    builder = build_from_bundle(bundle_root, {}, cache_key=("test", "missing"))
    # Default formula has 4 channels
    assert len(builder.channels) == 4
    clear_cache()


# ----------------------- IntersectionConfig serde -------------------------


def test_intersection_config_round_trip_v2():
    cfg = _make_v2_config()
    d = cfg.to_dict()
    cfg2 = IntersectionConfig.from_dict(d)
    assert cfg2.primary_cycle_id == 700001
    assert cfg2.cycles is not None
    assert cfg2.roads_static is not None

    mapping = cfg2.stage_to_std_phase_for_cycle(700001)
    assert mapping == {800001: 1, 800002: 4}


def test_intersection_config_stage_to_std_unknown_cycle_falls_back_to_primary():
    cfg = _make_v2_config()
    mapping = cfg.stage_to_std_phase_for_cycle(999999)  # cycle không có
    assert mapping == {800001: 1, 800002: 4}


def test_intersection_config_legacy_v1_no_cycles():
    cfg = IntersectionConfig(
        cross_id=1, direction_map={"1": 0}, phase_mapping=[0, 1],
    )
    # No cycles -> stage_to_std_phase returns None
    assert cfg.stage_to_std_phase_for_cycle(None) is None
    assert cfg.stage_to_std_phase_for_cycle(123) is None


def test_get_config_prefers_real_normalization_runtime_static(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    reset_settings_cache()
    clear_registry_cache()
    try:
        cfg_dir = tmp_path / "real_normalization" / "area_1308700" / "intersections"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "cross_33000000101005.json").write_text(
            json.dumps(
                {
                    "cross_id": 33000000101005,
                    "direction_map": {"700015": 0},
                    "observation_mask": [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    "primary_cycle_id": 1005,
                    "cycles": {
                        "1005": {
                            "stage_to_standard_phase": {"89501": 0},
                            "standard_phase_to_stage": {"0": 89501},
                            "is_primary": True,
                            "num_stages": 1,
                            "cycle_length": 106,
                            "yellow": 3,
                            "red_clear": 1,
                            "stages": [
                                {
                                    "id": 89501,
                                    "order_number": 1,
                                    "green": 102,
                                    "yellow": 3,
                                    "red_clear": 1,
                                }
                            ],
                        }
                    },
                    "roads_static": {
                        "700015": {
                            "lanes": 2,
                            "length_meters": 180,
                            "speed_design_kmh": 50,
                            "saturation_flow": 3600,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        cfg = get_config(1308700, 33000000101005)

        assert cfg is not None
        assert cfg.primary_cycle_id == 1005
        assert cfg.cycles is not None
        assert cfg.cycles["1005"]["cycle_length"] == 106
        assert cfg.roads_static is not None
        assert cfg.roads_static["700015"]["saturation_flow"] == 3600
    finally:
        clear_registry_cache()
        reset_settings_cache()
