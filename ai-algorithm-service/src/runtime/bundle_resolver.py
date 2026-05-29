"""Map area_id -> active bundle dir.

ai-runtime cache result trong TTL ngan (mac dinh 2s). Sau TTL, doc lai
active.json. Neu bundle_id moi, model_manager se reload ONNX.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.bundles import (
    ActivePointer,
    bundle_root,
    network_dir,
    read_active_pointer,
)
from src.core.config import get_settings
from src.db import repositories as repo
from src.db.base import get_session


@dataclass(frozen=True)
class ResolvedBundle:
    network_id: str
    pointer: ActivePointer
    bundle_path: Path

    @property
    def bundle_id(self) -> str:
        return self.pointer.bundle_id


_cache: Dict[int, Tuple[float, Optional[ResolvedBundle]]] = {}
_lock = threading.Lock()


def _network_for_area(area_id: int) -> Optional[str]:
    with get_session() as s:
        area = repo.get_area(s, area_id)
        if area is None:
            return None
        return area.network_id or f"area_{area_id}"


def resolve_active_bundle_for_area(area_id: int) -> Optional[ResolvedBundle]:
    settings = get_settings()
    if not settings.bundle_layout_enabled:
        return None

    ttl = settings.active_pointer_ttl_seconds
    now = time.time()
    with _lock:
        cached = _cache.get(area_id)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]

    network_id = _network_for_area(area_id)
    if network_id is None:
        with _lock:
            _cache[area_id] = (now, None)
        return None

    pointer = read_active_pointer(network_dir(network_id))
    if pointer is None:
        with _lock:
            _cache[area_id] = (now, None)
        return None

    path = bundle_root(network_id, pointer.bundle_id)
    if not path.exists():
        with _lock:
            _cache[area_id] = (now, None)
        return None

    resolved = ResolvedBundle(network_id=network_id, pointer=pointer, bundle_path=path)
    with _lock:
        _cache[area_id] = (now, resolved)
    return resolved


def resolve_active_bundle_for_network(network_id: str) -> Optional[ResolvedBundle]:
    pointer = read_active_pointer(network_dir(network_id))
    if pointer is None:
        return None
    path = bundle_root(network_id, pointer.bundle_id)
    if not path.exists():
        return None
    return ResolvedBundle(network_id=network_id, pointer=pointer, bundle_path=path)


def invalidate(area_id: Optional[int] = None) -> None:
    """Xoa cache resolver. Goi sau khi activate/rollback."""
    with _lock:
        if area_id is None:
            _cache.clear()
        else:
            _cache.pop(area_id, None)


def invalidate_network(network_id: str) -> None:
    """Xoa moi entry cache co network_id tuong ung."""
    # Tinh lai theo DB (don gian cho MVP).
    with get_session() as s:
        areas = repo.list_areas(s)
        target_ids = [a.area_id for a in areas if a.network_id == network_id]
    with _lock:
        for aid in target_ids:
            _cache.pop(aid, None)
