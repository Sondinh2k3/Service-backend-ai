# Configuration Reference

> Reference đầy đủ environment variables. Tất cả load qua `pydantic-settings` từ `.env.<APP_ENV>` hoặc set trực tiếp trong `docker-compose.yml`.

## 1. Cách config được load

Service đọc env theo thứ tự ưu tiên:
1. **Process environment** (set trong `docker-compose.yml`, shell, hoặc systemd)
2. **`.env.<APP_ENV>`** — file `.env.development`, `.env.production` ở thư mục gốc

`APP_ENV` mặc định = `development`. File `.env.<APP_ENV>` chỉ ảnh hưởng khi chạy local (`uv run uvicorn ...`). Khi chạy qua Docker, env trong `docker-compose.yml` ưu tiên cao nhất.

Code: [src/core/config.py](../src/core/config.py).

## 2. App settings

| Variable | Default | Mô tả |
|----------|---------|-------|
| `APP_ENV` | `development` | `development` / `production`. Xác định file `.env.X` để load |
| `DEBUG` | `false` | Bật debug log |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `SERVICE_NAME` | `ai-algorithm-service` | Tên service trong logs |

## 3. Service role (split ai-runtime / ai-ops)

| Variable | Default | Mô tả |
|----------|---------|-------|
| `SERVICE_ROLE` | `all` | `runtime` (chỉ inference) / `ops` (chỉ bundle lifecycle) / `all` (cả 2 trong 1 process — dev) |
| `RUNTIME_INTERNAL_URL` | _(empty)_ | URL ai-ops gọi ai-runtime để hot-reload (vd `http://ai-runtime:8000`). Trống nếu cùng process |

**Production khuyến nghị:** tách 2 container như `docker-compose.yml` mặc định.

## 4. Storage (Local + MinIO)

### 4.1 Local Model Storage

| Variable | Default | Mô tả |
|----------|---------|-------|
| `MODEL_DIR` | `models` | Thư mục root chứa bundles và legacy area files. Trong Docker thường là `/app/models` |

### 4.2 MinIO / S3

| Variable | Default | Mô tả |
|----------|---------|-------|
| `MINIO_ENABLED` | `false` | Bật MinIO integration |
| `MINIO_ENDPOINT` | _(empty)_ | Vd `minio:9000` hoặc `bundles.vendor.com` |
| `MINIO_ACCESS_KEY` | _(empty)_ | Access key (vendor cấp cho customer) |
| `MINIO_SECRET_KEY` | _(empty)_ | Secret key |
| `MINIO_BUCKET` | _(empty)_ | Tên bucket (vd `ai-models` hoặc `bundles`) |
| `MINIO_SECURE` | `false` | `true` nếu dùng HTTPS |
| `MINIO_REGION` | _(empty)_ | AWS region nếu dùng S3 |
| `MINIO_PREFIX` | _(empty)_ | Prefix mặc định khi upload (vd `models`) |
| `MINIO_UPLOAD_ON_SYNC` | `true` | Tự upload artifact lên MinIO khi sync (legacy flow) |

### 4.3 Auto-sync (Phase 3+)

| Variable | Default | Mô tả |
|----------|---------|-------|
| `MINIO_AUTO_SYNC_ENABLED` | `false` | Bật auto-deploy bundle khi xuất hiện trên MinIO |
| `MINIO_AUTO_SYNC_PREFIX` | _(empty)_ | Filter bucket prefix (vd `tenant_kh1/`). Trống = scan toàn bucket |
| `MINIO_AUTO_SYNC_SUFFIX` | `.zip` | Filter file extension |
| `MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS` | `600` | Safety-net poller interval. `0` = tắt poller |
| `MINIO_AUTO_SYNC_AUTO_ACTIVATE` | `true` | Activate ngay sau khi pull thành công |
| `MINIO_AUTO_SYNC_RECONNECT_SECONDS` | `5` | Initial reconnect delay khi listener disconnect |

Chi tiết: [auto-sync.md](auto-sync.md).

### 4.4 Sim Bundle composer

Các biến này bật pipeline mới: upload `*.sim.zip` chứa `sim_network.json` lên MinIO, ai-ops tự compile `real_normalization` từ `real_network_snapshot` trong DB nội bộ service, generate `deployment_map.json`, validate compatibility rồi compose runtime bundle.

| Variable | Default | Mô tả |
|----------|---------|-------|
| `SIM_BUNDLE_AUTO_COMPOSE_ENABLED` | `false` | Bật auto-detect Sim Bundle và compose Runtime Bundle |
| `SIM_BUNDLE_PREFIX` | _(empty)_ | Prefix listen/scan sim bundle. Local compose dùng `sim/default/` |
| `SIM_BUNDLE_SUFFIX` | `.sim.zip` | Suffix filter cho Sim Bundle |
| `SIM_BUNDLE_AUTO_ACTIVATE` | `true` | Activate Runtime Bundle sau khi compose thành công |
| `SIM_BUNDLE_UPLOAD_RUNTIME` | `true` | Upload Runtime Bundle đã compose lại MinIO để audit/redistribute |

> ⚠️ **Prefix safety**: Khi `SIM_BUNDLE_UPLOAD_RUNTIME=true`, runtime bundle composer tự upload lại lên MinIO. Nếu `SIM_BUNDLE_PREFIX` overlap với `ARTIFACT_BUNDLE_PREFIX` (xem §5), listener sẽ pickup runtime bundle vừa upload và compose lại → **vòng lặp vô hạn**. Service log warning lúc khởi động nếu phát hiện. Production khuyến nghị:
>
> ```env
> SIM_BUNDLE_PREFIX=sim/
> SIM_BUNDLE_SUFFIX=.sim.zip       # phải khác '.zip' generic
> ARTIFACT_BUNDLE_PREFIX=runtime/  # tách hoàn toàn khỏi sim/
> ```

> 📌 **Schema version**: Service chỉ chấp nhận sim bundle có `schema_version` thuộc tập `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` (hardcode trong [src/ops/sim_bundle.py](../src/ops/sim_bundle.py), hiện = `{1}`). Bundle ngoài tập → reject với error rõ ràng, không lưu DB. Khi upgrade schema, cập nhật cả service lẫn training team.

Local Docker Compose hiện set:

```env
MINIO_AUTO_SYNC_PREFIX=sim/default/
MINIO_AUTO_SYNC_SUFFIX=.sim.zip
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_PREFIX=sim/default/
SIM_BUNDLE_SUFFIX=.sim.zip
```

## 5. Bundle layout

| Variable | Default | Mô tả |
|----------|---------|-------|
| `BUNDLE_LAYOUT_ENABLED` | `true` | Bật bundle layout (Phase 1+). Tắt → fallback legacy `area_<id>/` |
| `DEFAULT_TENANT_ID` | `default` | Tenant mặc định cho area chưa có |
| `ARTIFACT_BUNDLE_PREFIX` | `bundles` | Prefix MinIO khi push bundle |
| `ACTIVE_POINTER_TTL_SECONDS` | `2.0` | ai-runtime poll `active.json` mỗi N giây |

## 6. Strict mode + safety

| Variable | Default | Mô tả |
|----------|---------|-------|
| `AI_STRICT_MODE` | `false` | Production: `true`. Fail-fast khi config / file thiếu, không auto-generate |
| `ENFORCE_SINGLE_AREA_PER_REQUEST` | `true` | Yêu cầu 1 area / request (theo contract Lớp 1) |
| `STARTUP_PREFLIGHT` | `true` | Chạy preflight cho mọi network có active.json khi service start |

## 7. Guardrails (Defense in Depth — Lớp 4)

| Variable | Default | Mô tả |
|----------|---------|-------|
| `GUARDRAIL_ENABLED` | `true` | Bật guardrails layer |
| `GUARDRAIL_MIN_GREEN` | `10` | Min green time (giây) — clip dưới |
| `GUARDRAIL_MAX_GREEN` | `60` | Max green time — clip trên |
| `GUARDRAIL_ANTI_STARVATION_MAX_SKIPS` | `3` | Số lần liên tiếp ở min trước khi bump |
| `GUARDRAIL_ANTI_STARVATION_RECOVERY_GREEN` | `15` | Green time khi bump anti-starvation |

## 8. Drift Detection (Phase 3+)

| Variable | Default | Mô tả |
|----------|---------|-------|
| `DRIFT_ENABLED` | `true` | Bật drift detection trong AIService |
| `DRIFT_PSI_THRESHOLD` | `0.2` | PSI threshold trigger drift event |
| `DRIFT_KS_THRESHOLD` | `0.1` | KS threshold trigger drift event |
| `DRIFT_MIN_SAMPLES` | `200` | Số samples tối thiểu để check + size baseline runtime warmup |
| `DRIFT_CHECK_INTERVAL` | `100` | Mỗi N inference request, run check 1 lần |
| `DRIFT_WINDOW_SIZE` | `1000` | Sliding window max. `0` = unbounded |

Code: [src/observability/drift_registry.py](../src/observability/drift_registry.py).

## 9. Database

| Variable | Default | Mô tả |
|----------|---------|-------|
| `DATABASE_URL` | `sqlite:///./ai_service.db` | SQLAlchemy URL |
| `DB_ECHO` | `false` | Log SQL queries (debug) |

**Production examples:**
- MySQL: `mysql+pymysql://user:pass@host:3306/db`
- Postgres: `postgresql+psycopg://user:pass@host:5432/db`
- SQLite (Edge gọn nhẹ): `sqlite:///./ai_service.db`

⚠️ Trong `docker-compose.yml`, `DATABASE_URL` được hardcode ở section `environment:` của ai-runtime/ai-ops — KHÔNG đọc từ `.env`. Nếu muốn đổi, sửa trực tiếp compose.

## 10. Auth

| Variable | Default | Mô tả |
|----------|---------|-------|
| `INTERNAL_API_KEY` | _(empty)_ | API key cho `/internal/*` và `/ops/*`. Trống = bypass auth (dev mode) |
| `INTERNAL_API_KEY_HEADER` | `X-Internal-API-Key` | HTTP header name |

⚠️ **Production:** phải set `INTERNAL_API_KEY` random per-customer.

## 11. Telemetry

| Variable | Default | Mô tả |
|----------|---------|-------|
| `TELEMETRY_ENABLED` | `true` | Bật middleware ghi request_id + latency |
| `REQUEST_ID_HEADER` | `X-Request-Id` | Header chứa request_id (auto-gen UUID nếu thiếu) |

## 12. MLflow (optional, Lớp 3)

| Variable | Default | Mô tả |
|----------|---------|-------|
| `MLFLOW_ENABLED` | `false` | Bật MLflow tracking + registry |
| `MLFLOW_TRACKING_URI` | _(empty)_ | MLflow server URL |
| `MLFLOW_REGISTRY_URI` | _(empty)_ | Registry URL (nếu khác tracking) |
| `MLFLOW_EXPERIMENT_NAME` | `rlops-traffic-signal` | Tên experiment |

Cần cài optional extra: `uv sync --extra mlflow` (nặng ~200MB).

## 13. Profiles ví dụ

### 13.1 Development local (SQLite, MinIO tắt)

`.env.development`:
```env
APP_ENV=development
SERVICE_ROLE=all
DEBUG=true
DATABASE_URL=sqlite:///./ai_service.db
INTERNAL_API_KEY=dev-secret
MINIO_ENABLED=false
AI_STRICT_MODE=false
```

Run: `uv run uvicorn src.main:app --reload`

### 13.2 Customer Edge production

`.env.production`:
```env
APP_ENV=production
SERVICE_ROLE=all
DEBUG=false
DATABASE_URL=sqlite:///./ai_service.db

# Vendor MinIO (read-only credentials)
MINIO_ENABLED=true
MINIO_ENDPOINT=bundles.vendor.com
MINIO_ACCESS_KEY=kh1_access_key
MINIO_SECRET_KEY=<from vendor>
MINIO_BUCKET=bundles
MINIO_SECURE=true
MINIO_PREFIX=tenant_kh1

# Auto-sync (key feature)
MINIO_AUTO_SYNC_ENABLED=true
MINIO_AUTO_SYNC_PREFIX=sim/tenant_kh1/
MINIO_AUTO_SYNC_SUFFIX=.sim.zip
MINIO_AUTO_SYNC_AUTO_ACTIVATE=true
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_PREFIX=sim/tenant_kh1/
SIM_BUNDLE_SUFFIX=.sim.zip

# Strict + auth
AI_STRICT_MODE=true
STARTUP_PREFLIGHT=true
INTERNAL_API_KEY=<random per customer>

# Tắt MLflow ở Edge (chỉ vendor cloud cần)
MLFLOW_ENABLED=false
```

### 13.3 Vendor cloud production (cho Lớp 3 packager)

```env
APP_ENV=production
SERVICE_ROLE=ops                          # chỉ ops, không inference
DATABASE_URL=postgresql+psycopg://...

MINIO_ENABLED=true
MINIO_ENDPOINT=minio.internal:9000        # MinIO của vendor
MINIO_BUCKET=bundles
MINIO_PREFIX=                             # vendor scan toàn bucket

MLFLOW_ENABLED=true
MLFLOW_TRACKING_URI=http://mlflow.internal:5000
```

## 14. Cách verify env trong container

```powershell
# List env vars trong container ai-runtime
docker exec ai_runtime env | Select-String "MINIO|DRIFT|GUARDRAIL"

# Print 1 setting
docker exec ai_ops sh -c 'echo "$MINIO_AUTO_SYNC_ENABLED"'

# Reload toàn bộ settings cache (sau khi sửa env)
docker compose restart ai-ops ai-runtime
```

## 15. Tham khảo

- [src/core/config.py](../src/core/config.py) — Pydantic Settings class với defaults
- [.env.example](../.env.example) — template với comments
- [docker-compose.yml](../docker-compose.yml) — env hardcoded cho từng service
