# Configuration

All settings are loaded from environment variables. If `.env.<APP_ENV>` exists, it is loaded automatically.

## 1. App and role

| Variable | Default | Production note |
|---|---|---|
| `APP_ENV` | `development` | Use `production` |
| `DEBUG` | `false` | Keep false |
| `SERVICE_NAME` | `ai-algorithm-service` | Useful for logs |
| `SERVICE_ROLE` | `all` | Use `runtime` or `ops` in production |
| `HOST` | `0.0.0.0` | Usually unchanged |
| `PORT` | `8000` | Compose maps runtime/ops ports |

Production split:

```text
ai-runtime: SERVICE_ROLE=runtime
ai-ops:     SERVICE_ROLE=ops
```

## 2. Auth

| Variable | Default | Production note |
|---|---|---|
| `INTERNAL_API_KEY` | empty/demo in compose | Set strong per-customer key |
| `INTERNAL_API_KEY_HEADER` | `X-Internal-API-Key` | Usually unchanged |
| `REQUEST_ID_HEADER` | `X-Request-Id` | Use for audit/trace |

Internal sync and ops APIs require the internal API key.

## 3. Strict mode and safety

| Variable | Default | Production note |
|---|---|---|
| `AI_STRICT_MODE` | `false` | Use `true` |
| `ENFORCE_SINGLE_AREA_PER_REQUEST` | `true` | Keep true |
| `STARTUP_PREFLIGHT` | `true` | Keep true |
| `GUARDRAIL_ENABLED` | `true` | Keep true |
| `GUARDRAIL_MIN_GREEN` | `10` | Match TSC safety rules |
| `GUARDRAIL_MAX_GREEN` | `90` | Match TSC safety rules |
| `GUARDRAIL_ANTI_STARVATION_MAX_SKIPS` | `3` | Tune with field ops |
| `GUARDRAIL_ANTI_STARVATION_RECOVERY_GREEN` | `15` | Tune with field ops |

## 4. Storage and database

| Variable | Default | Production note |
|---|---|---|
| `MODEL_DIR` | `models` | Persistent volume |
| `DATABASE_URL` | `sqlite:///./ai_service.db` | SQLite ok for edge MVP; external DB optional |
| `DB_ECHO` | `false` | Keep false |
| `BUNDLE_LAYOUT_ENABLED` | `true` | Keep true |
| `DEFAULT_TENANT_ID` | `default` | Override per customer |
| `ARTIFACT_BUNDLE_PREFIX` | `bundles` | Runtime bundle prefix |
| `ACTIVE_POINTER_TTL_SECONDS` | `2.0` | Runtime hot reload polling/cache TTL |

## 5. MinIO/S3

| Variable | Default | Note |
|---|---|---|
| `MINIO_ENABLED` | `false` | Enable for artifact store |
| `MINIO_ENDPOINT` | empty | Host:port or URL |
| `MINIO_ACCESS_KEY` | empty | Read-only for customer edge |
| `MINIO_SECRET_KEY` | empty | Secret |
| `MINIO_BUCKET` | empty | Artifact bucket |
| `MINIO_SECURE` | `false` | Use true for HTTPS |
| `MINIO_REGION` | empty | Optional |
| `MINIO_PREFIX` | empty | Optional common prefix |
| `MINIO_UPLOAD_ON_SYNC` | `true` | Upload synced artifacts if enabled |

## 6. Auto-sync

| Variable | Default | Production note |
|---|---|---|
| `MINIO_AUTO_SYNC_ENABLED` | `false` | Enable on ai-ops if using MinIO |
| `MINIO_AUTO_SYNC_PREFIX` | empty | Scope to one tenant/network |
| `MINIO_AUTO_SYNC_SUFFIX` | `.zip` | Usually unchanged |
| `MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS` | `600` | Safety-net scan |
| `MINIO_AUTO_SYNC_AUTO_ACTIVATE` | `true` | Prefer false in production |
| `MINIO_AUTO_SYNC_RECONNECT_SECONDS` | `5` | Listener reconnect backoff |

## 7. Sim bundle composer

| Variable | Default | Production note |
|---|---|---|
| `SIM_BUNDLE_AUTO_COMPOSE_ENABLED` | `false` | Enable on ai-ops for sim bundles |
| `SIM_BUNDLE_PREFIX` | empty | Example: `sim/tenant_kh1/network_001` |
| `SIM_BUNDLE_SUFFIX` | `.sim.zip` | Recommended |
| `SIM_BUNDLE_AUTO_ACTIVATE` | `true` | Use `false` in production |
| `SIM_BUNDLE_UPLOAD_RUNTIME` | `true` | Keep true for audit/reuse |

Production should review `compatibility_report.json` before activation.

## 8. Drift and telemetry

| Variable | Default | Note |
|---|---|---|
| `DRIFT_ENABLED` | `true` | Runtime drift detector |
| `DRIFT_PSI_THRESHOLD` | `0.2` | PSI threshold |
| `DRIFT_KS_THRESHOLD` | `0.1` | KS threshold |
| `DRIFT_MIN_SAMPLES` | `200` | Warmup size |
| `DRIFT_CHECK_INTERVAL` | `100` | Check every N inferences |
| `DRIFT_WINDOW_SIZE` | `1000` | Sliding window |
| `TELEMETRY_ENABLED` | `true` | Request id/log/metrics middleware |

## 9. MLflow

| Variable | Default | Note |
|---|---|---|
| `MLFLOW_ENABLED` | `false` | Usually vendor/training side only |
| `MLFLOW_TRACKING_URI` | empty | Tracking server |
| `MLFLOW_REGISTRY_URI` | empty | Registry |
| `MLFLOW_EXPERIMENT_NAME` | `rlops-traffic-signal` | Experiment name |

## 10. Example production edge

```env
APP_ENV=production
AI_STRICT_MODE=true
SERVICE_ROLE=runtime
MODEL_DIR=/var/lib/ai-algorithm-service/models
DATABASE_URL=sqlite:////var/lib/ai-algorithm-service/ai_service.db
INTERNAL_API_KEY=<strong-key>
MINIO_ENABLED=true
MINIO_ENDPOINT=minio.vendor.example
MINIO_SECURE=true
MINIO_BUCKET=ai-models
MINIO_AUTO_SYNC_ENABLED=true
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_AUTO_ACTIVATE=false
```

## 11. Verify in container

```bash
docker compose exec ai-runtime env | sort
docker compose exec ai-runtime python -c "from src.core.config import get_settings; print(get_settings().model_dump())"
```

## 12. References

- [deployment.md](deployment.md)
- [auto-sync.md](auto-sync.md)
- [troubleshooting.md](troubleshooting.md)
