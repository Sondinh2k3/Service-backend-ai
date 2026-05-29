"""Register a demo real-network snapshot from bundle-tooling's Cologne3 map.

This is the fastest path for the local E2E demo because the generated snapshot
is intentionally compatible with `examples/cologne3/intersection_config.json`.
For a real system, use `/internal/sync/areas/{area_id}/real-network` with the
selected area/cross/road/cycle/stage data from the control service.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


DIR_TO_DB = {"N": 1, "E": 2, "S": 3, "W": 4}


def build_payload(*, deployment_map_path: Path, service_area_id: int, tenant_id: str, network_id: str) -> dict:
    deployment_map = json.loads(deployment_map_path.read_text(encoding="utf-8"))
    area_crosses = []
    crosses = []
    roads = []
    cycles = []
    stages = []
    sim_to_real = {}

    for idx, cross in enumerate(deployment_map["crosses"], start=1):
        real_cross_id = int(cross["real_cross_id"])
        primary = cross["cycles"][0]
        real_cycle_id = int(primary["real_cycle_id"])
        sim_to_real[str(cross["sim_tls_id"])] = real_cross_id
        area_crosses.append(
            {
                "area_id": service_area_id,
                "cross_id": real_cross_id,
                "cycle_id": real_cycle_id,
                "type": 1,
                "is_active": 1,
                "description": cross.get("notes", ""),
            }
        )
        crosses.append(
            {
                "id": real_cross_id,
                "is_active": 1,
                "cross_name": f"Demo Cologne3 {cross['sim_tls_id']}",
                "location": f"0,{idx}",
                "old_id": str(cross["sim_tls_id"]),
                "area_id": service_area_id,
            }
        )
        cycles.append(
            {
                "id": real_cycle_id,
                "is_active": 1,
                "cross_id": real_cross_id,
                "cycle_name": f"Primary cycle {real_cross_id}",
                "cycle_type": 0,
                "number_of_stages": len(primary["phase_to_stage"]),
            }
        )
        for order, item in enumerate(primary["phase_to_stage"], start=1):
            stages.append(
                {
                    "id": int(item["real_stage_id"]),
                    "is_active": 1,
                    "order_number": order,
                    "cycle_id": real_cycle_id,
                    "stage_code": f"P{order}",
                    "old_id": str(item["sim_phase_idx"]),
                    "min_green_time": 15,
                    "max_green_time": 80,
                }
            )
        for direction, road in (cross.get("roads_by_direction") or {}).items():
            if road is None:
                continue
            roads.append(
                {
                    "id": int(road["real_road_id"]),
                    "is_active": 1,
                    "road_name": f"{cross['sim_tls_id']} {direction}",
                    "from_cross": real_cross_id,
                    "from_cross_direction": DIR_TO_DB[direction],
                    "to_cross": None,
                    "to_cross_direction": None,
                    "number_of_lanes": int(road.get("real_lanes") or road.get("sim_lanes") or 1),
                    "length": float(road.get("length_meters") or 100.0),
                    "speed_design": float(road.get("speed_design_kmh") or 50.0),
                    "capacity_design": float(road.get("saturation_flow") or 1800.0),
                }
            )

    return {
        "sourceEventId": f"demo-real-network-{service_area_id}-{network_id}",
        "tenantId": tenant_id,
        "networkId": network_id,
        "schemaVersion": "real-network/v1",
        "sourceVersion": f"deployment-map:{deployment_map_path.name}",
        "area": {
            "area_id": service_area_id,
            "area_name": f"Demo Area {network_id}",
            "area_code": network_id,
        },
        "areaCrosses": area_crosses,
        "crosses": crosses,
        "roads": roads,
        "cycles": cycles,
        "stages": stages,
        "simToReal": sim_to_real,
    }


def push_payload(*, ops_url: str, api_key: str, service_area_id: int, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{ops_url.rstrip('/')}/internal/sync/areas/{service_area_id}/real-network",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json", "X-Internal-API-Key": api_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deployment-map",
        type=Path,
        default=root.parent / "bundle-tooling" / "examples" / "cologne3" / "deployment_map.example.json",
    )
    parser.add_argument("--service-area-id", type=int, default=1)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--network-id", default="cologne3")
    parser.add_argument("--ops-url", default="http://localhost:8002")
    parser.add_argument("--api-key", default="sondinh2k3")
    parser.add_argument("--output-json", type=Path, default=root / "dist" / "demo_real_network_snapshot.json")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    payload = build_payload(
        deployment_map_path=args.deployment_map,
        service_area_id=args.service_area_id,
        tenant_id=args.tenant_id,
        network_id=args.network_id,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[demo-real-network] wrote {args.output_json}")

    if args.no_push:
        return 0
    result = push_payload(
        ops_url=args.ops_url,
        api_key=args.api_key,
        service_area_id=args.service_area_id,
        payload=payload,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
