"""MLflow Tracking + Registry helper (Lop 3 — slice cho MVP).

Dung MLflow cho:
  - Tracking moi training run (metrics, params, artifacts).
  - Model Registry: dang ky bundle moi tao se trace duoc qua run_id.

KHONG dung MLflow Projects, KHONG dung MLflow Deployments. Bundle tu phat trien
o `src/bundles/` van la single source of truth tren Edge.

Module nay best-effort: neu mlflow chua cai hoac tracking_uri trong, ham log
khong fail — service van chay.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from src.core.config import get_settings
from src.core.logger import logger

try:
    import mlflow  # type: ignore
    _MLFLOW_OK = True
except Exception:  # pragma: no cover
    mlflow = None  # type: ignore[assignment]
    _MLFLOW_OK = False


def is_enabled() -> bool:
    settings = get_settings()
    return bool(_MLFLOW_OK and settings.mlflow_enabled and settings.mlflow_tracking_uri)


def _setup() -> bool:
    if not is_enabled():
        return False
    settings = get_settings()
    try:
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        if settings.mlflow_registry_uri:
            mlflow.set_registry_uri(settings.mlflow_registry_uri)
        mlflow.set_experiment(settings.mlflow_experiment_name)
        return True
    except Exception as e:
        logger.warning(f"[mlflow] setup failed: {e}")
        return False


@contextmanager
def start_run(run_name: Optional[str] = None, tags: Optional[Dict[str, str]] = None) -> Iterator[Any]:
    """Context manager bao ham mlflow.start_run. Neu disabled, yield None."""
    if not _setup():
        yield None
        return
    try:
        with mlflow.start_run(run_name=run_name, tags=tags or {}) as run:
            yield run
    except Exception as e:
        logger.warning(f"[mlflow] start_run failed: {e}")
        yield None


def log_params(params: Dict[str, Any]) -> None:
    if not _setup():
        return
    try:
        mlflow.log_params({k: str(v) for k, v in params.items()})
    except Exception as e:
        logger.warning(f"[mlflow] log_params failed: {e}")


def log_metrics(metrics: Dict[str, float], step: Optional[int] = None) -> None:
    if not _setup():
        return
    try:
        mlflow.log_metrics({k: float(v) for k, v in metrics.items()}, step=step)
    except Exception as e:
        logger.warning(f"[mlflow] log_metrics failed: {e}")


def log_artifact(path: Path, artifact_path: Optional[str] = None) -> None:
    if not _setup():
        return
    try:
        mlflow.log_artifact(str(path), artifact_path=artifact_path)
    except Exception as e:
        logger.warning(f"[mlflow] log_artifact failed: {e}")


def register_bundle(
    *,
    bundle_zip: Path,
    manifest: Dict[str, Any],
    model_name: str,
) -> Optional[str]:
    """Log bundle ZIP nhu mot artifact + tao model version trong Registry.

    Tra model version (string) neu thanh cong, None neu skip.
    """
    if not _setup():
        return None
    try:
        with mlflow.start_run(run_name=f"package-{manifest.get('bundle_id')}") as run:
            mlflow.log_dict(manifest, "model_manifest.json")
            mlflow.log_artifact(str(bundle_zip), artifact_path="bundle")
            mlflow.set_tags({
                "tenant_id": manifest.get("tenant_id", ""),
                "network_id": manifest.get("network_id", ""),
                "version": manifest.get("version", ""),
                "topology_hash": manifest.get("topology_hash", ""),
            })
            artifact_uri = f"runs:/{run.info.run_id}/bundle/{bundle_zip.name}"
            mv = mlflow.register_model(model_uri=artifact_uri, name=model_name)
            return str(mv.version)
    except Exception as e:
        logger.warning(f"[mlflow] register_bundle failed: {e}")
        return None
