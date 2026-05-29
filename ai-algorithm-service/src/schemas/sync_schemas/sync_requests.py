"""Schema cho /internal/sync/* endpoints (plan 5.2).

Moi request co `sourceEventId` lam idempotency key (plan 4.3.3).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AreaUpsert(BaseModel):
    sourceEventId: str = Field(..., min_length=1, max_length=128)
    areaName: str = ""
    isActive: bool = True
    controllerVisible: bool = True
    tenantId: Optional[str] = Field(default=None, min_length=1, max_length=64)
    networkId: Optional[str] = Field(default=None, min_length=1, max_length=128)


class AreaArtifactSync(BaseModel):
    sourceEventId: str = Field(..., min_length=1, max_length=128)
    policyVersion: str = Field(..., min_length=1, max_length=64)
    configVersion: str = Field(..., min_length=1, max_length=64)
    # File paths: central backend phai trust service runtime path ve file;
    # neu khong gui, service tu resolve tu model_dir / area_<id>.
    policyPath: Optional[str] = None
    metaPath: Optional[str] = None
    networkPath: Optional[str] = None
    checksum: Optional[str] = None
    activate: bool = False


class CrossConfigSync(BaseModel):
    sourceEventId: str = Field(..., min_length=1, max_length=128)
    configVersion: str = "1"
    directionMap: Dict[str, int] = Field(default_factory=dict)
    phaseMapping: Optional[List[int]] = None
    observationMask: Optional[List[int]] = None

    def to_config_payload(self, cross_id: int) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"cross_id": cross_id}
        if self.directionMap:
            payload["direction_map"] = {str(k): int(v) for k, v in self.directionMap.items()}
        if self.phaseMapping is not None:
            payload["phase_mapping"] = list(self.phaseMapping)
        if self.observationMask is not None:
            payload["observation_mask"] = list(self.observationMask)
        return payload


class RealNetworkSnapshotSync(BaseModel):
    """Payload service-owned cho vung dieu khien thuc te.

    Cac list ben duoi co chu y mirror cac bang lien quan trong `management.sql`.
    Service luu snapshot nay vao DB noi bo, sau do ai-ops compile
    `real_normalization.json` tu snapshot thay vi phu thuoc truc tiep DB quan ly.
    """

    sourceEventId: str = Field(..., min_length=1, max_length=128)
    tenantId: Optional[str] = Field(default=None, min_length=1, max_length=64)
    networkId: Optional[str] = Field(default=None, min_length=1, max_length=128)
    schemaVersion: str = Field(default="real-network/v1", min_length=1, max_length=32)
    sourceVersion: Optional[str] = Field(default=None, max_length=128)
    area: Dict[str, Any] = Field(default_factory=dict)
    areaCrosses: List[Dict[str, Any]] = Field(default_factory=list)
    crosses: List[Dict[str, Any]] = Field(default_factory=list)
    roads: List[Dict[str, Any]] = Field(default_factory=list)
    cycles: List[Dict[str, Any]] = Field(default_factory=list)
    stages: List[Dict[str, Any]] = Field(default_factory=list)
    simToReal: Dict[str, Any] = Field(default_factory=dict)


class FinalizeSync(BaseModel):
    sourceEventId: Optional[str] = None
    areaIds: Optional[List[int]] = None
