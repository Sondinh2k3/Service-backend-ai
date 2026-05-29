"""
Artifact storage helper.

- Local filesystem by default.
- Optional MinIO download-on-demand when MINIO_ENABLED=true.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from src.core.config import get_settings
from src.core.logger import logger

try:
    from minio import Minio
    from minio.error import S3Error
except Exception:  # pragma: no cover - optional dependency for MinIO
    Minio = None  # type: ignore[assignment]
    S3Error = Exception  # type: ignore[assignment]


_MINIO_CLIENT = None
_MINIO_CLIENT_ERROR: Optional[str] = None


def is_minio_enabled() -> bool:
    return bool(get_settings().minio_enabled)


def resolve_local_path(source_path: Optional[str], default_local: Path) -> Path:
    if not source_path:
        return default_local
    if _parse_object_uri(source_path) is not None:
        return default_local
    return Path(source_path)


def ensure_local_file(local_path: Path, source_path: Optional[str] = None) -> Path:
    if local_path.exists():
        return local_path

    obj = _resolve_object_location(source_path, local_path)
    if not obj:
        return local_path

    client = _get_minio_client()
    if client is None:
        return local_path

    bucket, key = obj
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.fget_object(bucket, key, str(local_path))
        logger.info(f"Downloaded MinIO object {bucket}/{key} -> {local_path}")
    except S3Error as exc:
        logger.warning(f"MinIO download failed for {bucket}/{key}: {exc}")
    except Exception as exc:
        logger.warning(f"MinIO download failed for {bucket}/{key}: {exc}")
    return local_path


def upload_local_file(local_path: Path, source_path: Optional[str] = None) -> None:
    settings = get_settings()
    if not settings.minio_enabled or not settings.minio_upload_on_sync:
        return
    if not local_path.exists():
        if source_path and _parse_object_uri(source_path) is not None:
            return
        logger.warning(f"MinIO upload skipped, local file missing: {local_path}")
        return

    obj = _resolve_object_location(source_path, local_path)
    if not obj:
        return

    client = _get_minio_client()
    if client is None:
        return

    bucket, key = obj
    try:
        client.fput_object(bucket, key, str(local_path))
        logger.info(f"Uploaded MinIO object {bucket}/{key} <- {local_path}")
    except S3Error as exc:
        logger.warning(f"MinIO upload failed for {bucket}/{key}: {exc}")
    except Exception as exc:
        logger.warning(f"MinIO upload failed for {bucket}/{key}: {exc}")


def exists(local_path: Path, source_path: Optional[str] = None) -> bool:
    if local_path.exists():
        return True

    obj = _resolve_object_location(source_path, local_path)
    if not obj:
        return False

    client = _get_minio_client()
    if client is None:
        return False

    bucket, key = obj
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error:
        return False
    except Exception:
        return False


def download_uri(uri: str, local_path: Path) -> Path:
    """Download s3:// URI ve `local_path`. Raise neu MinIO disabled hoac fail."""
    parsed = _parse_object_uri(uri)
    if parsed is None:
        raise ValueError(f"URI khong hop le (yeu cau s3:// hoac minio://): {uri}")
    bucket, key = parsed
    client = _get_minio_client()
    if client is None:
        raise RuntimeError(
            f"MinIO khong san sang: {_MINIO_CLIENT_ERROR or 'disabled'}"
        )
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.fget_object(bucket, key, str(local_path))
    logger.info(f"Downloaded {bucket}/{key} -> {local_path}")
    return local_path


def upload_uri(local_path: Path, uri: str) -> None:
    """Upload `local_path` len s3:// URI. Raise neu fail."""
    parsed = _parse_object_uri(uri)
    if parsed is None:
        raise ValueError(f"URI khong hop le (yeu cau s3:// hoac minio://): {uri}")
    bucket, key = parsed
    client = _get_minio_client()
    if client is None:
        raise RuntimeError(
            f"MinIO khong san sang: {_MINIO_CLIENT_ERROR or 'disabled'}"
        )
    if not Path(local_path).exists():
        raise FileNotFoundError(local_path)
    client.fput_object(bucket, key, str(local_path))
    logger.info(f"Uploaded {bucket}/{key} <- {local_path}")


def list_remote_zips(
    prefix: str = "",
    suffix: str = ".zip",
) -> list[str]:
    """List object URIs ('s3://<bucket>/<key>') trong MinIO bucket khop suffix.

    Empty list neu MinIO disabled / fail. Khong raise — caller xu ly im lang.
    """
    settings = get_settings()
    if not settings.minio_enabled:
        return []
    client = _get_minio_client()
    if client is None:
        return []
    bucket = settings.minio_bucket
    if not bucket:
        return []

    out: list[str] = []
    try:
        for obj in client.list_objects(bucket, prefix=prefix or None, recursive=True):
            name = obj.object_name or ""
            if suffix and not name.endswith(suffix):
                continue
            out.append(f"s3://{bucket}/{name}")
    except S3Error as exc:
        logger.warning(f"MinIO list_objects failed: {exc}")
    except Exception as exc:
        logger.warning(f"MinIO list_objects failed: {exc}")
    return out


def listen_remote_zips(
    prefix: str = "",
    suffix: str = ".zip",
    events: tuple[str, ...] = ("s3:ObjectCreated:*",),
):
    """Generator yield ('s3://<bucket>/<key>', event_dict) khi co object moi.

    Su dung MinIO listen_bucket_notification (S3 SDK long-poll). Generator block
    cho den khi co event hoac connection close.

    Caller phai chay trong thread rieng (sync API). Re-raise khi MinIO loi de
    caller decide reconnect strategy.
    """
    settings = get_settings()
    client = _get_minio_client()
    if client is None or not settings.minio_enabled:
        return
    bucket = settings.minio_bucket
    if not bucket:
        return

    with client.listen_bucket_notification(
        bucket,
        prefix=prefix or "",
        suffix=suffix or "",
        events=list(events),
    ) as iterator:
        for event in iterator:
            for record in event.get("Records", []) or []:
                try:
                    key = record["s3"]["object"]["key"]
                except (KeyError, TypeError):
                    continue
                yield f"s3://{bucket}/{key}", record


def object_exists(uri: str) -> bool:
    parsed = _parse_object_uri(uri)
    if parsed is None:
        return False
    bucket, key = parsed
    client = _get_minio_client()
    if client is None:
        return False
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error:
        return False
    except Exception:
        return False


def _resolve_object_location(
    source_path: Optional[str],
    local_path: Path,
) -> Optional[Tuple[str, str]]:
    settings = get_settings()
    if not settings.minio_enabled:
        return None

    if source_path:
        parsed = _parse_object_uri(source_path)
        if parsed is not None:
            return parsed
        key = _path_to_key(Path(source_path))
    else:
        key = _path_to_key(local_path)

    bucket = settings.minio_bucket
    if not bucket or not key:
        return None
    return bucket, key


def _get_minio_client():
    global _MINIO_CLIENT
    global _MINIO_CLIENT_ERROR

    if _MINIO_CLIENT_ERROR:
        return None
    if _MINIO_CLIENT is not None:
        return _MINIO_CLIENT

    settings = get_settings()
    if not settings.minio_enabled:
        return None

    if Minio is None:
        _MINIO_CLIENT_ERROR = "MinIO enabled but dependency is missing (minio)."
        logger.error(_MINIO_CLIENT_ERROR)
        return None

    missing = []
    if not settings.minio_endpoint:
        missing.append("MINIO_ENDPOINT")
    if not settings.minio_access_key:
        missing.append("MINIO_ACCESS_KEY")
    if not settings.minio_secret_key:
        missing.append("MINIO_SECRET_KEY")
    if not settings.minio_bucket:
        missing.append("MINIO_BUCKET")

    if missing:
        _MINIO_CLIENT_ERROR = "MinIO enabled but missing settings: " + ", ".join(missing)
        logger.error(_MINIO_CLIENT_ERROR)
        return None

    endpoint = _normalize_endpoint(settings.minio_endpoint)
    if not endpoint:
        _MINIO_CLIENT_ERROR = "MinIO endpoint is empty after normalization."
        logger.error(_MINIO_CLIENT_ERROR)
        return None

    _MINIO_CLIENT = Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=bool(settings.minio_secure),
        region=settings.minio_region or None,
    )
    return _MINIO_CLIENT


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme:
        return parsed.netloc or parsed.path
    return endpoint


def _parse_object_uri(uri: str) -> Optional[Tuple[str, str]]:
    if uri.startswith("s3://") or uri.startswith("minio://"):
        parsed = urlparse(uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if bucket and key:
            return bucket, key
    return None


def _path_to_key(path: Path) -> str:
    settings = get_settings()
    model_dir_raw = Path(settings.model_dir)
    model_dir_abs = model_dir_raw if model_dir_raw.is_absolute() else Path.cwd() / model_dir_raw

    if path.is_absolute():
        try:
            rel = path.relative_to(model_dir_abs)
        except ValueError:
            rel = path
    else:
        rel = path
        if not model_dir_raw.is_absolute():
            raw_parts = model_dir_raw.parts
            if raw_parts and rel.parts[: len(raw_parts)] == raw_parts:
                rel = Path(*rel.parts[len(raw_parts) :])

    key = rel.as_posix().lstrip("/")
    return _apply_prefix(key)


def _apply_prefix(key: str) -> str:
    prefix = get_settings().minio_prefix.strip("/")
    if not prefix:
        return key
    if key == prefix or key.startswith(prefix + "/"):
        return key
    return f"{prefix}/{key}"
