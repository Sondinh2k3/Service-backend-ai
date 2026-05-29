"""Backward-compat shim: re-export topology_hash từ shared package."""

from __future__ import annotations

from traffic_rl_features.bundle.topology_hash import compute_topology_hash

__all__ = ["compute_topology_hash"]
