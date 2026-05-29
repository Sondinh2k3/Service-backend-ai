"""Build and push a service-owned real network snapshot from management DB views.

Demo usage:
  python scripts/register_real_network_snapshot.py \
    --db-url mysql+pymysql://root:123456@localhost:3306/statistic \
    --source-area-id 1308556 \
    --service-area-id 1 \
    --tenant-id default \
    --network-id cologne3 \
    --ops-url http://localhost:8002 \
    --api-key sondinh2k3
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text


def _fetch_all(engine, sql: str, params: dict) -> list[dict]:
    stmt = text(sql)
    if "ids" in params:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(stmt, params).mappings().all()
    return [dict(r) for r in rows]


def _fetch_one(engine, sql: str, params: dict) -> dict:
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else {}


def build_snapshot(*, db_url: str, source_area_id: int, service_area_id: int, tenant_id: str, network_id: str) -> dict:
    engine = create_engine(db_url, future=True)
    area = _fetch_one(engine, "SELECT * FROM v_area WHERE AREA_ID = :area_id", {"area_id": source_area_id})
    area["source_area_id"] = source_area_id
    area["area_id"] = service_area_id

    area_crosses = _fetch_all(
        engine,
        """
        SELECT *
        FROM v_area_cross
        WHERE area_id = :area_id AND (is_active = 1 OR is_active IS NULL)
        """,
        {"area_id": source_area_id},
    )
    for row in area_crosses:
        row["source_area_id"] = source_area_id
        row["area_id"] = service_area_id

    cross_ids = sorted({int(row["cross_id"]) for row in area_crosses if row.get("cross_id") is not None})
    if not cross_ids:
        raise RuntimeError(f"No active crosses found for source area {source_area_id}")

    crosses = _fetch_all(
        engine,
        "SELECT * FROM v_cross WHERE id IN :ids AND (is_active = 1 OR is_active IS NULL)",
        {"ids": tuple(cross_ids)},
    )
    roads = _fetch_all(
        engine,
        """
        SELECT *
        FROM v_road
        WHERE (from_cross IN :ids OR to_cross IN :ids)
          AND (is_active = 1 OR is_active IS NULL)
        """,
        {"ids": tuple(cross_ids)},
    )
    cycles = _fetch_all(
        engine,
        "SELECT * FROM v_cycle WHERE cross_id IN :ids AND (is_active = 1 OR is_active IS NULL)",
        {"ids": tuple(cross_ids)},
    )
    cycle_ids = sorted({int(row["id"]) for row in cycles if row.get("id") is not None})
    stages = _fetch_all(
        engine,
        """
        SELECT *
        FROM v_stage
        WHERE cycle_id IN :ids AND (is_active = 1 OR is_active IS NULL)
        ORDER BY cycle_id, order_number
        """,
        {"ids": tuple(cycle_ids)},
    )

    return {
        "sourceEventId": f"real-network-{service_area_id}-{network_id}",
        "tenantId": tenant_id,
        "networkId": network_id,
        "schemaVersion": "real-network/v1",
        "sourceVersion": f"management-area-{source_area_id}",
        "area": area,
        "areaCrosses": area_crosses,
        "crosses": crosses,
        "roads": roads,
        "cycles": cycles,
        "stages": stages,
        "simToReal": {},
    }


def push_snapshot(*, ops_url: str, api_key: str, service_area_id: int, payload: dict) -> dict:
    url = f"{ops_url.rstrip('/')}/internal/sync/areas/{service_area_id}/real-network"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "X-Internal-API-Key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--source-area-id", type=int, required=True)
    parser.add_argument("--service-area-id", type=int, required=True)
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--network-id", required=True)
    parser.add_argument("--ops-url", default="http://localhost:8002")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    payload = build_snapshot(
        db_url=args.db_url,
        source_area_id=args.source_area_id,
        service_area_id=args.service_area_id,
        tenant_id=args.tenant_id,
        network_id=args.network_id,
    )

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[snapshot] wrote {args.output_json}")

    if args.no_push:
        print("[snapshot] no-push enabled")
        return 0

    if not args.api_key:
        raise RuntimeError("--api-key is required unless --no-push is set")
    result = push_snapshot(
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
