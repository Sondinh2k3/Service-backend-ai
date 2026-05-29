"""Test topology hash — phat hien drift cau truc duong."""
from __future__ import annotations

import json

from src.bundles.topology_hash import compute_topology_hash


def _write_network(tmp_path, data: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "network.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_same_network_same_hash(tmp_path):
    data = {
        "intersections": [
            {"id": "c1", "neighbors": [{"neighbor_id": "c2", "direction": 1}]},
            {"id": "c2", "neighbors": [{"neighbor_id": "c1", "direction": 3}]},
        ],
    }
    p1 = _write_network(tmp_path / "a", data)
    p2 = _write_network(tmp_path / "b", data)
    assert compute_topology_hash(p1) == compute_topology_hash(p2)


def test_different_topology_different_hash(tmp_path):
    a = _write_network(tmp_path / "a", {
        "intersections": [{"id": "c1", "neighbors": []}],
    })
    b = _write_network(tmp_path / "b", {
        "intersections": [
            {"id": "c1", "neighbors": []},
            {"id": "c2", "neighbors": []},
        ],
    })
    assert compute_topology_hash(a) != compute_topology_hash(b)


def test_canonical_ordering_independent(tmp_path):
    """Doi thu tu key/list khong doi hash."""
    a = _write_network(tmp_path / "a", {
        "intersections": [
            {"id": "alpha", "direction": 1},
            {"id": "beta", "direction": 3},
        ],
    })
    b = _write_network(tmp_path / "b", {
        "intersections": [
            {"id": "beta", "direction": 3},
            {"id": "alpha", "direction": 1},
        ],
    })
    assert compute_topology_hash(a) == compute_topology_hash(b)


def test_ephemeral_fields_ignored(tmp_path):
    """Field khong thuoc _STRUCTURAL_KEYS (vd timestamps, comments) khong anh huong hash."""
    base = {"intersections": [{"id": "c1", "neighbors": []}]}
    a = _write_network(tmp_path / "a", base)
    b_data = dict(base)
    b_data["created_at"] = "2026-05-08T00:00:00Z"
    b_data["comment"] = "version notes"
    b = _write_network(tmp_path / "b", b_data)
    assert compute_topology_hash(a) == compute_topology_hash(b)


def test_dict_input_equivalent_to_file(tmp_path):
    data = {"intersections": [{"id": "c1", "neighbors": []}]}
    p = _write_network(tmp_path, data)
    assert compute_topology_hash(p) == compute_topology_hash(data)


def test_empty_intersections_stable(tmp_path):
    a = _write_network(tmp_path / "a", {"intersections": []})
    b = _write_network(tmp_path / "b", {"intersections": []})
    assert compute_topology_hash(a) == compute_topology_hash(b)
