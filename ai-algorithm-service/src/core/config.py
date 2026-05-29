"""Runtime settings for AI Algorithm Service.

Toan bo cau hinh doc tu environment (ho tro .env.<APP_ENV>).
Theo plan muc 6.1: strict mode, local DB, auth, telemetry.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ----- App
    app_env: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    service_name: str = "ai-algorithm-service"

    # ----- Artifact storage
    model_dir: str = "models"

    # ----- MinIO (optional, S3-compatible)
    minio_enabled: bool = False
    minio_endpoint: Optional[str] = None
    minio_access_key: Optional[str] = None
    minio_secret_key: Optional[str] = None
    minio_bucket: Optional[str] = None
    minio_secure: bool = False
    minio_region: Optional[str] = None
    minio_prefix: str = ""
    minio_upload_on_sync: bool = True

    # Auto-sync (Lop 2 — auto pull bundle moi khi xuat hien tren MinIO).
    # Ket hop 2 co che de instant + reliable:
    #   1. Listener: dung MinIO listen_bucket_notification (S3 SDK long-poll
    #      stream) — outbound only, latency ~1-2s.
    #   2. Safety-net poller: scan bucket dinh ky de bat su kien bi miss khi
    #      listener disconnect.
    minio_auto_sync_enabled: bool = False
    # Prefix tren MinIO de scan (vd 'tenant_kh1/'). De trong = scan toan bucket.
    minio_auto_sync_prefix: str = ""
    # Suffix file de filter (chi xu ly .zip).
    minio_auto_sync_suffix: str = ".zip"
    # Safety-net poller scan interval (giay). 0 = disable poller, chi dung listener.
    minio_auto_sync_poll_interval_seconds: int = 600
    # Auto-activate bundle ngay sau khi pull thanh cong.
    minio_auto_sync_auto_activate: bool = True
    # Khi listener disconnect, doi N giay roi reconnect (exponential backoff cap).
    minio_auto_sync_reconnect_seconds: int = 5

    # ----- Sim Bundle -> Runtime Bundle composer (CI/CD in-service)
    # Bat auto-compose khi phat hien sim bundle tren MinIO.
    sim_bundle_auto_compose_enabled: bool = False
    # Prefix de listen sim bundle (vd 'sim/default/'). Empty = reuse minio_auto_sync_prefix.
    sim_bundle_prefix: str = ""
    # Suffix filter (khuyen nghi '.sim.zip' de phan biet voi runtime bundle).
    sim_bundle_suffix: str = ".sim.zip"
    # Auto-activate runtime bundle sau khi compose thanh cong.
    sim_bundle_auto_activate: bool = True
    # Upload runtime bundle len MinIO (de audit / redistribute).
    sim_bundle_upload_runtime: bool = True

    # ----- Strict mode (plan 6.1.1)
    # Production: khong auto-generate config, thieu file la fail-fast.
    ai_strict_mode: bool = False
    # Giai han 1 area/request (plan 6.1.5). Khi tat, cho phep nhieu area mot request.
    enforce_single_area_per_request: bool = True

    # ----- RLOps Lop 2 — Model Bundle
    # Bat layout bundle (Local Model Storage chia theo networks/<id>/bundles/<bid>).
    # Khi tat, runtime fallback ve layout legacy <model_dir>/area_<id>/.
    bundle_layout_enabled: bool = True
    # Default tenant cho area chua co tenant_id rieng.
    default_tenant_id: str = "default"
    # MinIO prefix cho bundle ZIP. Layout: {prefix}/{tenant}/{network}/{version}/bundle.zip.
    artifact_bundle_prefix: str = "bundles"
    # ai-runtime poll active.json moi bao nhieu giay (TTL cache).
    active_pointer_ttl_seconds: float = 2.0

    # ----- Guardrails (Lop 4 — Safety Layer)
    guardrail_enabled: bool = True
    guardrail_min_green: int = 10
    guardrail_max_green: int = 90
    guardrail_anti_starvation_max_skips: int = 3
    guardrail_anti_starvation_recovery_green: int = 15

    # ----- ai-ops vs ai-runtime split
    # Service role cua process hien tai: 'runtime' | 'ops' | 'all'. 'all' chay
    # ca 2 router trong 1 process (dev/MVP). Production tach 2 container.
    service_role: str = "all"
    # ai-ops noi voi ai-runtime hot-reload qua HTTP. Khi ai-ops va ai-runtime
    # cung process (role=all), de trong de skip.
    runtime_internal_url: Optional[str] = None

    # ----- MLflow (optional, Lop 3)
    mlflow_enabled: bool = False
    mlflow_tracking_uri: Optional[str] = None
    mlflow_registry_uri: Optional[str] = None
    mlflow_experiment_name: str = "rlops-traffic-signal"

    # ----- Drift Detection (Lop 4)
    drift_enabled: bool = True
    drift_psi_threshold: float = 0.2
    drift_ks_threshold: float = 0.1
    # Toi thieu samples de check (cung la kich thuoc baseline runtime warmup).
    drift_min_samples: int = 200
    # Moi N inference request, run check() 1 lan.
    drift_check_interval: int = 100
    # Sliding window toi da (bo sample cu nhat khi vuot). 0 = unbounded.
    drift_window_size: int = 1000

    # ----- Local DB (plan 4)
    # Default: SQLite file trong thu muc service de chay local. Production co the
    # override bang DATABASE_URL (vd MySQL/Postgres).
    database_url: str = "sqlite:///./ai_service.db"
    db_echo: bool = False

    # ----- Auth (plan 6.2.1)
    # API key cho endpoint noi bo. Neu rong -> bo qua kiem tra (dev mode).
    internal_api_key: Optional[str] = None
    internal_api_key_header: str = "X-Internal-API-Key"

    # ----- Startup preflight
    # When enabled, ai-runtime runs preflight on every network with an Active bundle
    # at startup. Combined with strict mode, a failing preflight aborts startup.
    startup_preflight: bool = True

    # ----- Telemetry (plan 6.2.4)
    telemetry_enabled: bool = True
    request_id_header: str = "X-Request-Id"

    class Config:
        env_file_encoding = "utf-8"
        case_sensitive = False

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")


_settings_cache: Optional[Settings] = None


def get_settings() -> Settings:
    """Cached settings loader. Doc .env.<APP_ENV> neu co."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache

    env = os.getenv("APP_ENV", "development")
    env_file = f".env.{env}"
    if os.path.exists(env_file):
        _settings_cache = Settings(_env_file=env_file)
    else:
        _settings_cache = Settings()
    return _settings_cache


def reset_settings_cache() -> None:
    """Xoa cache (dung cho test)."""
    global _settings_cache
    _settings_cache = None
