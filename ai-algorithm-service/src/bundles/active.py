"""Active Pointer — chỉ ra bundle nào đang phục vụ inference.

`active.json` đặt tại `<model_dir>/networks/<network_id>/active.json`. Cấu trúc:
{
  "bundle_id": "...",
  "version": "v1.2.3",
  "topology_hash": "...",
  "previous_bundle_id": "...",     # null nếu chưa từng có bundle nào
  "activated_at": "ISO-8601 UTC"
}

ai-runtime đọc file này (poll mỗi vài giây) để phát hiện swap. Nếu bundle_id
khác cache, runtime sẽ chạy Preflight rồi reload.

Ghi atomic bằng write-temp + rename (POSIX-safe; trên Windows os.replace là
atomic ở cấp filesystem cho cùng volume).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


ACTIVE_FILENAME = "active.json"


@dataclass
class ActivePointer:
    bundle_id: str
    version: str
    topology_hash: str
    activated_at: str = ""
    previous_bundle_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.activated_at:
            self.activated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ActivePointer":
        return cls(
            bundle_id=str(data["bundle_id"]),
            version=str(data.get("version", "")),
            topology_hash=str(data.get("topology_hash", "")),
            activated_at=str(data.get("activated_at", "")),
            previous_bundle_id=data.get("previous_bundle_id"),
        )


def active_pointer_path(network_root: Path) -> Path:
    return Path(network_root) / ACTIVE_FILENAME


def read_active_pointer(network_root: Path) -> Optional[ActivePointer]:
    path = active_pointer_path(network_root)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ActivePointer.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def write_active_pointer(network_root: Path, pointer: ActivePointer) -> Path:
    """Ghi atomic active.json. Trả về path."""
    network_root = Path(network_root)
    network_root.mkdir(parents=True, exist_ok=True)
    target = active_pointer_path(network_root)

    payload = json.dumps(pointer.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".active_", suffix=".json.tmp", dir=str(network_root)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target
