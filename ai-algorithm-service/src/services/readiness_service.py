"""Readiness service (plan 6.1.2, 11.3.1).

Check xem mot area co du artifact/bundle de inference khong:
  - area co trong DB + is_active
  - co active runtime bundle theo area.network_id
  - bundle co policy.onnx + policy_meta.json + network.json tren disk

Tra ve cau truc chuan cho API `/readiness` + manifest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from src.core.config import get_settings
from src.bundles import bundle_root, network_dir, read_active_pointer
from src.db import repositories as repo
from src.db.base import get_session


@dataclass
class AreaReadinessCheck:
    areaId: int
    areaName: str
    isActive: bool
    controllerVisible: bool
    ready: bool
    hasPolicy: bool
    hasMeta: bool
    hasNetwork: bool
    policyVersion: Optional[str]
    configVersion: Optional[str]
    missing: List[str]
    source: str = "bundle"
    activeBundleId: Optional[str] = None
    networkId: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _check_bundle_paths(bundle_path: Path) -> dict:
    return {
        "hasPolicy": (bundle_path / "policy.onnx").exists(),
        "hasMeta": (bundle_path / "policy_meta.json").exists(),
        "hasNetwork": (bundle_path / "network.json").exists(),
    }


def check_area(area_id: int) -> AreaReadinessCheck:
    """Compute readiness cho 1 area. Tra ve struct AreaReadinessCheck."""
    with get_session() as session:
        area = repo.get_area(session, area_id)
        if area is None:
            return AreaReadinessCheck(
                areaId=area_id,
                areaName="",
                isActive=False,
                controllerVisible=False,
                ready=False,
                hasPolicy=False,
                hasMeta=False,
                hasNetwork=False,
                policyVersion=None,
                configVersion=None,
                missing=["area_registry"],
                source="none",
            )

        network_id = area.network_id or f"area_{area_id}"
        pointer = read_active_pointer(network_dir(network_id))
        if pointer is None:
            return AreaReadinessCheck(
                areaId=area.area_id,
                areaName=area.area_name or "",
                isActive=area.is_active,
                controllerVisible=area.controller_visible,
                ready=False,
                hasPolicy=False,
                hasMeta=False,
                hasNetwork=False,
                policyVersion=None,
                configVersion=None,
                missing=["active_bundle_pointer"],
                source="bundle",
                networkId=network_id,
            )

        bundle_path = bundle_root(network_id, pointer.bundle_id)
        paths = _check_bundle_paths(bundle_path)
        missing: List[str] = []
        if not area.is_active:
            missing.append("area_inactive")
        if not bundle_path.exists():
            missing.append("active_bundle_dir")
        if not paths["hasPolicy"]:
            missing.append("policy.onnx")
        if not paths["hasMeta"]:
            missing.append("policy_meta.json")
        if not paths["hasNetwork"]:
            missing.append("network.json")

        ready = (
            area.is_active
            and bundle_path.exists()
            and paths["hasPolicy"]
            and paths["hasMeta"]
            and paths["hasNetwork"]
        )
        return AreaReadinessCheck(
            areaId=area.area_id,
            areaName=area.area_name or "",
            isActive=area.is_active,
            controllerVisible=area.controller_visible,
            ready=ready,
            hasPolicy=paths["hasPolicy"],
            hasMeta=paths["hasMeta"],
            hasNetwork=paths["hasNetwork"],
            policyVersion=pointer.version,
            configVersion=None,
            missing=missing,
            source="bundle",
            activeBundleId=pointer.bundle_id,
            networkId=network_id,
        )


def list_visible_manifest() -> List[dict]:
    """Manifest rut gon cho control software (plan 11.3.3).

    Chi tra area: is_active + controller_visible + ready.
    """
    out: List[dict] = []
    with get_session() as session:
        area_ids = [
            area.area_id
            for area in repo.list_areas(session, only_active=True, only_visible=True)
        ]

    for area_id in area_ids:
        check = check_area(area_id)
        if not check.ready:
            continue
        out.append(
            {
                "areaId": check.areaId,
                "areaName": check.areaName,
                "networkId": check.networkId,
                "source": check.source,
                "activeBundleId": check.activeBundleId,
                "policyVersion": check.policyVersion,
                "configVersion": check.configVersion,
                "ready": True,
            }
        )
    return out


def service_ready() -> dict:
    """Readiness son service: true neu >=1 area ready (hoac khong co area nao trong strict mode -> false).

    Plan 11.3.1: AI_STRICT_MODE + co area invalid -> service readiness=false.
    """
    settings = get_settings()
    with get_session() as session:
        areas = repo.list_areas(session, only_active=True)
        checks = [check_area(a.area_id) for a in areas]

    invalid = [c for c in checks if not c.ready]
    ready_count = sum(1 for c in checks if c.ready)

    if settings.ai_strict_mode and invalid:
        return {
            "ready": False,
            "reason": "STRICT_MODE_INVALID_AREAS",
            "totalAreas": len(checks),
            "readyAreas": ready_count,
            "invalidAreas": [c.areaId for c in invalid],
        }

    if not checks:
        return {
            "ready": not settings.ai_strict_mode,
            "reason": "NO_AREAS_REGISTERED",
            "totalAreas": 0,
            "readyAreas": 0,
            "invalidAreas": [],
        }

    return {
        "ready": ready_count > 0,
        "totalAreas": len(checks),
        "readyAreas": ready_count,
        "invalidAreas": [c.areaId for c in invalid],
    }
