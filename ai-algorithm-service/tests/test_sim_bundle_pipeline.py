from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import reset_settings_cache
from src.core.exception import AlgorithmException
from src.db.base import Base
from src.db import repositories as repo
from src.ops.composer import (
    ComposeError,
    _resolve_sim_to_real_mapping,
    build_deployment_map_from_real_normalization,
)
from src.ops.real_normalization import compile_real_normalization
from src.ops.sim_bundle import (
    SIM_BUNDLE_MANIFEST_FILENAME,
    validate_sim_bundle_dir,
)
from src.schemas.sync_schemas.sync_requests import RealNetworkSnapshotSync
from src.services.sync_service import sync_real_network_snapshot


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT.parent / "bundle-tooling" / "examples" / "cologne3"
SIM_NETWORK = EXAMPLES / "intersection_config.json"
DEPLOYMENT_MAP_EXAMPLE = EXAMPLES / "deployment_map.example.json"


def _real_normalization_from_deployment_map() -> dict:
    dm = json.loads(DEPLOYMENT_MAP_EXAMPLE.read_text(encoding="utf-8"))
    crosses = []
    for cross in dm["crosses"]:
        direction_map = {}
        roads_static = {}
        for idx, direction in enumerate(("N", "E", "S", "W")):
            road = cross["roads_by_direction"].get(direction)
            if road is None:
                continue
            rid = str(road["real_road_id"])
            direction_map[rid] = idx
            roads_static[rid] = {
                "lanes": road["real_lanes"],
                "length_meters": road.get("length_meters") or 100.0,
                "speed_design_kmh": road.get("speed_design_kmh") or 50.0,
                "saturation_flow": road.get("saturation_flow") or 1800.0,
            }
        cycles = {}
        for cycle in cross["cycles"]:
            stage_to_std = {
                str(item["real_stage_id"]): item["std_phase_idx"]
                for item in cycle["phase_to_stage"]
            }
            cycles[str(cycle["real_cycle_id"])] = {
                "stage_to_standard_phase": stage_to_std,
                "standard_phase_to_stage": {
                    str(item["std_phase_idx"]): item["real_stage_id"]
                    for item in cycle["phase_to_stage"]
                },
                "is_primary": cycle["is_primary"],
                "num_stages": len(cycle["phase_to_stage"]),
            }
        crosses.append(
            {
                "real_cross_id": cross["real_cross_id"],
                "sim_tls_id": cross["sim_tls_id"],
                "primary_cycle_id": cross["cycles"][0]["real_cycle_id"],
                "direction_map": direction_map,
                "roads_static": roads_static,
                "cycles": cycles,
            }
        )
    return {
        "area_id": dm["area_id"],
        "crosses": crosses,
    }


def test_validate_sim_bundle_keeps_legacy_intersection_config(tmp_path: Path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "intersection_config.json").write_text("{}", encoding="utf-8")
    (root / "policy.onnx").write_bytes(b"fake")
    (root / "policy_meta.json").write_text("{}", encoding="utf-8")
    (root / SIM_BUNDLE_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "sim_bundle_id": "sim-legacy",
                "tenant_id": "default",
                "network_id": "legacy_net",
                "version": "v1",
                "sim_config_path": "intersection_config.json",
                "policy_onnx_path": "policy.onnx",
                "policy_meta_path": "policy_meta.json",
            }
        ),
        encoding="utf-8",
    )

    manifest = validate_sim_bundle_dir(root)
    assert manifest.sim_network_path == "intersection_config.json"


def test_composer_generates_valid_deployment_map_from_real_normalization(tmp_path: Path):
    real_norm_path = tmp_path / "real_normalization.json"
    real_norm_path.write_text(
        json.dumps(_real_normalization_from_deployment_map()),
        encoding="utf-8",
    )

    deployment_map, report = build_deployment_map_from_real_normalization(
        sim_network_path=SIM_NETWORK,
        real_normalization_path=real_norm_path,
        area_id=1,
        network_id="cologne3",
        created_by="test",
    )

    assert report["summary"]["errors"] == 0
    assert deployment_map["network_id"] == "cologne3"
    assert deployment_map["sim_config_path"] == "sim_network.json"
    assert len(deployment_map["crosses"]) == 5
    first = deployment_map["crosses"][0]
    assert first["sim_tls_id"] == "33202549"
    assert first["real_cross_id"] == 567001
    assert first["cycles"][0]["phase_to_stage"][0]["real_stage_id"] == 800001


def test_real_normalization_prefers_service_owned_snapshot(tmp_path: Path):
    db_path = tmp_path / "ai_service.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)

    snapshot = {
        "area_id": 1,
        "tenant_id": "default",
        "network_id": "cologne3",
        "schema_version": "real-network/v1",
        "area": {"area_id": 1, "area_name": "Demo"},
        "area_crosses": [
            {"area_id": 1, "cross_id": 101, "cycle_id": 1001, "is_active": 1},
            {"area_id": 1, "cross_id": 102, "cycle_id": 1002, "is_active": 1},
        ],
        "crosses": [
            {"id": 101, "cross_name": "A", "location": "0,0", "is_active": 1},
            {"id": 102, "cross_name": "B", "location": "0,1", "is_active": 1},
        ],
        "roads": [
            {
                "id": 201,
                "from_cross": 101,
                "from_cross_direction": 1,
                "to_cross": 102,
                "to_cross_direction": 3,
                "number_of_lanes": 2,
                "length": 120,
                "speed_design": 50,
                "capacity_design": 1800,
                "is_active": 1,
            }
        ],
        "cycles": [
            {"id": 1001, "cross_id": 101, "cycle_type": 0, "is_active": 1},
            {"id": 1002, "cross_id": 102, "cycle_type": 0, "is_active": 1},
        ],
        "stages": [
            {"id": 3001, "cycle_id": 1001, "order_number": 1, "is_active": 1},
            {"id": 3002, "cycle_id": 1001, "order_number": 2, "is_active": 1},
            {"id": 3003, "cycle_id": 1002, "order_number": 1, "is_active": 1},
            {"id": 3004, "cycle_id": 1002, "order_number": 2, "is_active": 1},
        ],
        "sim_to_real": {"33202549": 101},
    }

    payload_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    with Session(engine) as session:
        repo.upsert_area(
            session,
            area_id=1,
            area_name="Demo",
            tenant_id="default",
            network_id="cologne3",
        )
        repo.upsert_real_network_snapshot(
            session,
            area_id=1,
            tenant_id="default",
            network_id="cologne3",
            schema_version="real-network/v1",
            payload_json=payload_json,
            checksum="test",
        )
        session.commit()

    payload = compile_real_normalization(
        db_url=f"sqlite:///{db_path}",
        area_id=1,
        output_dir=tmp_path / "real_norm",
    )

    assert payload["source"] == "service_snapshot"
    assert payload["network_id"] == "cologne3"
    assert payload["sim_to_real"] == {"33202549": 101}
    assert [c["real_cross_id"] for c in payload["crosses"]] == [101, 102]
    assert payload["crosses"][0]["cycles"]["1001"]["num_stages"] == 2


def test_real_network_snapshot_accepts_snake_case_sim_to_real():
    body = RealNetworkSnapshotSync.model_validate(
        {
            "sourceEventId": "evt-1",
            "areaCrosses": [{"area_id": 1, "cross_id": 101}],
            "crosses": [{"id": 101}],
            "cycles": [{"id": 1001, "cross_id": 101}],
            "stages": [{"id": 3001, "cycle_id": 1001}],
            "sim_to_real": {"33202549": 101},
        }
    )

    assert body.simToReal == {"33202549": 101}


def test_real_network_snapshot_accepts_nested_payload():
    body = RealNetworkSnapshotSync.model_validate(
        {
            "sourceEventId": "evt-nested-1",
            "tenantId": "default",
            "networkId": "nested-net",
            "schemaVersion": "real-network/v2",
            "area": {"id": 1, "name": "Nested Area"},
            "crosses": [
                {
                    "id": 101,
                    "simId": "sim-101",
                    "location": "21.0,105.0",
                    "primaryCycleId": 1001,
                    "roads": [
                        {
                            "id": 201,
                            "direction": 1,
                            "toCrossId": 102,
                            "lanes": 2,
                            "length": 120,
                            "capacity": 3600,
                            "speedDesign": 50,
                            "coordinates": [[21.001, 105.0], [21.0, 105.0]],
                        }
                    ],
                    "cycles": [
                        {
                            "id": 1001,
                            "type": 0,
                            "length": 90,
                            "yellow": 3,
                            "redClear": 1,
                            "stages": [
                                {
                                    "id": 3001,
                                    "order": 1,
                                    "oldId": "0",
                                    "green": 40,
                                    "yellow": 3,
                                    "redClear": 1,
                                    "minGreen": 15,
                                    "maxGreen": 90,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert body.area == {"id": 1, "name": "Nested Area", "area_id": 1, "area_name": "Nested Area"}
    assert body.areaCrosses == [{"cross_id": 101, "area_id": 1, "cycle_id": 1001}]
    assert body.crosses == [{"id": 101, "location": "21.0,105.0"}]
    assert body.roads[0]["from_cross"] == 101
    assert body.roads[0]["from_cross_direction"] == 1
    assert body.roads[0]["to_cross"] == 102
    assert body.roads[0]["number_of_lanes"] == 2
    assert body.roads[0]["capacity_design"] == 3600
    assert body.roads[0]["speed_design"] == 50
    assert body.roads[0]["coordinates"] == [
        {"order_number": 1, "latitude": 21.001, "longitude": 105.0},
        {"order_number": 2, "latitude": 21.0, "longitude": 105.0},
    ]
    assert body.cycles == [
        {
            "id": 1001,
            "cross_id": 101,
            "cycle_type": 0,
            "cycle_length": 90,
            "yellow": 3,
            "red_clear": 1,
        }
    ]
    assert body.stages == [
        {
            "id": 3001,
            "cycle_id": 1001,
            "order_number": 1,
            "old_id": "0",
            "green": 40,
            "yellow": 3,
            "red_clear": 1,
            "min_green_time": 15,
            "max_green_time": 90,
        }
    ]
    assert body.simToReal == {"sim-101": 101}


def test_sync_real_network_rejects_sim_to_real_outside_snapshot():
    with pytest.raises(AlgorithmException) as exc:
        sync_real_network_snapshot(
            area_id=1,
            tenant_id="default",
            network_id="net-1",
            schema_version="real-network/v1",
            source_version=None,
            area={"area_id": 1},
            area_crosses=[{"area_id": 1, "cross_id": 101}],
            crosses=[{"id": 101}],
            roads=[],
            cycles=[{"id": 1001, "cross_id": 101}],
            stages=[{"id": 3001, "cycle_id": 1001}],
            sim_to_real={"33202549": 999},
            source_event_id="evt-invalid-map",
        )

    assert exc.value.extra["invalidMapping"][0]["reason"] == "REAL_CROSS_NOT_IN_SNAPSHOT"


def test_production_rejects_order_based_sim_to_real_fallback(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    reset_settings_cache()
    try:
        with pytest.raises(ComposeError, match="Production khong cho phep auto-map"):
            _resolve_sim_to_real_mapping(
                sim_ids=["sim-a"],
                real_crosses=[{"real_cross_id": 101}],
                real_norm={},
            )
    finally:
        reset_settings_cache()
