# Auto-Sync — Auto-deploy Bundle từ MinIO

> Cơ chế tự động pull bundle khi xuất hiện trên MinIO bucket. Với pipeline mới, ai-ops ưu tiên nhận **Sim Bundle** (`*.sim.zip`) chứa `policy.onnx`, `policy_meta.json`, `sim_network.json`; tự compile `real_normalization` từ `real_network_snapshot` trong DB nội bộ service, generate `deployment_map.json`, validate compatibility, compose **Runtime Bundle**, rồi activate.

> 👉 Xem thêm [PIPELINE.md](PIPELINE.md) cho luồng end-to-end đầy đủ, bao gồm cách xử lý race-condition giữa Sim Bundle và Real Snapshot.

## 1. Cơ chế

Auto-sync kết hợp **2 cơ chế song song** để vừa instant vừa reliable:

### 1.1 Listener (instant, ~1-2s latency)

Dùng MinIO Python SDK `listen_bucket_notification()` — long-poll stream qua HTTPS. Edge mở 1 connection outbound đến MinIO; MinIO stream events S3 (`s3:ObjectCreated:*`) về khi có file mới.

```
Edge ai-ops                      Vendor MinIO
    │                                  │
    │  GET /<bucket>?notification=...  │
    ├─────────────────────────────────►│  (open long-poll connection)
    │                                  │
    │                                  │  ◄── PUT /<bucket>/.../area_x.sim.zip
    │                                  │       (vendor upload bundle)
    │                                  │
    │  ◄ event { Records: [...] }      │
    │  (stream JSON event)             │
    │                                  │
    │  pull → validate → activate      │
    │                                  │
```

**Đặc điểm:**
- Outbound only — xuyên NAT/firewall khách hàng
- Latency ~1-2s từ lúc upload đến lúc service serve bundle mới
- Không cần config MinIO server (không cần `mc event add` hay webhook)
- Tự động reconnect với exponential backoff khi connection drop

### 1.2 Safety-net poller (10 phút)

Async task scan toàn bucket định kỳ. Bắt event bị miss khi:
- Listener chưa kịp reconnect sau disconnect
- Bundle upload trong window ai-ops restart
- Race conditions hiếm gặp

Default interval 600s (10 phút) — chỉ là safety net, không phải primary mechanism.

### 1.3 Tại sao chọn listener thay vì webhook

Bảng so sánh các cách trigger:

| Cách | Latency | Network | MinIO config | Phù hợp |
|------|---------|---------|--------------|---------|
| Polling 60s | 30-60s | Outbound | Không | Đơn giản nhưng wasteful |
| Webhook `notify_webhook` | 1-2s | **Inbound** ❌ | Cần `mc event add` | Khi MinIO + ai-ops cùng network |
| **`listen_bucket_notification`** | **1-2s** | **Outbound** ✅ | **Không cần** | **Vendor cloud + customer edge** |

Webhook không phù hợp khi MinIO ở vendor cloud, ai-ops ở customer edge sau NAT — MinIO không reach được vào edge. Listener khắc phục bằng cách edge mở connection ra trước.

## 2. Configuration

Tất cả config ở [src/core/config.py](../src/core/config.py) section "Auto-sync":

| Env var | Default | Mô tả |
|---------|---------|-------|
| `MINIO_AUTO_SYNC_ENABLED` | `false` | Bật auto-sync |
| `MINIO_AUTO_SYNC_PREFIX` | `""` | Prefix bucket scan (vd `tenant_kh1/`). Trống = scan toàn bucket |
| `MINIO_AUTO_SYNC_SUFFIX` | `.zip` | Filter file extension |
| `MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS` | `600` | Safety-net poller interval. `0` = tắt poller |
| `MINIO_AUTO_SYNC_AUTO_ACTIVATE` | `true` | Activate ngay sau pull (vs chỉ validate) |
| `MINIO_AUTO_SYNC_RECONNECT_SECONDS` | `5` | Initial reconnect delay khi listener drop. Exponential up to 60s |

Các env riêng cho Sim Bundle composer:

| Env var | Default | Mô tả |
|---------|---------|-------|
| `SIM_BUNDLE_AUTO_COMPOSE_ENABLED` | `false` | Bật auto-detect/compose Sim Bundle |
| `SIM_BUNDLE_PREFIX` | `""` | Prefix listen sim bundle. Docker Compose local dùng `sim/default/` |
| `SIM_BUNDLE_SUFFIX` | `.sim.zip` | Chỉ nhận file sim bundle |
| `SIM_BUNDLE_AUTO_ACTIVATE` | `true` | Activate runtime bundle sau khi compose |
| `SIM_BUNDLE_UPLOAD_RUNTIME` | `true` | Upload runtime bundle đã compose lại MinIO để audit/redistribute |

**Phụ thuộc:**
- `MINIO_ENABLED=true`
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
- `SERVICE_ROLE=ops` hoặc `all` (auto-sync chỉ start trong process role ops)

## 3. Workflow

### 3.1 Vendor side

```powershell
# Build sim bundle từ training outputs — chạy trong repo training Service-ai
cd Service-ai
python scripts/build_sim_bundle.py `
  --tenant-id tenant_kh1 `
  --network-id area_x `
  --version 1.2.0 `
  --network area_x `
  --policy-onnx tmp\onnx_eval\policy.onnx `
  --policy-meta tmp\onnx_eval\policy_meta.json `
  --output-zip dist\area_x.sim.zip

# Push MinIO vào prefix listener
mc cp dist/area_x.sim.zip vendor/ai-models/sim/tenant_kh1/area_x/area_x.sim.zip
# DONE
```

### 3.2 Edge side (tự động, không cần làm gì)

Điều kiện trước khi Sim Bundle tới: backend/controller đã gọi `PUT /internal/sync/areas/{area_id}/real-network` để lưu real network snapshot cho cùng `tenant_id/network_id`.

Trong logs ai-ops:
```
[auto-sync] Listener detected: s3://ai-models/sim/tenant_kh1/area_x/area_x.sim.zip
[auto-sync] Pulling new bundle: s3://...
[composer] Built runtime bundle ...
[ops] Activated bundle area_x-1.2.0-abc123 for network area_x
[runtime] Preflight ok network=area_x bundle=area_x-1.2.0-abc123 version=1.2.0
[auto-sync] pulled_count=1
```

Sau ~1-2s, inference tiếp theo dùng bundle v1.2.0.

### 3.3 Sim Bundle về TRƯỚC Real Snapshot — `pending_real_snapshot`

Đây là race-condition được xử lý tự động. Nếu listener pickup sim bundle nhưng service chưa có `real_network_snapshot` cho `(tenant_id, network_id)` đó:

```
[auto-sync] Pulling new bundle: s3://ai-models/sim/default/cologne3/cologne3-v1.2.0.sim.zip
[ops] Sim bundle sim-cologne3-abc cho network=cologne3 cho real snapshot.
      Se retry tu dong khi controller upload snapshot.
```

Bundle được lưu vào DB với `bundle_kind='sim'`, `status='pending_real_snapshot'`. BundleEvent `compose-deferred` ghi rõ lý do.

Khi controller gọi `PUT /internal/sync/areas/{area_id}/real-network` cho `(tenant_id, network_id)` đó, service tự động:

1. Lưu snapshot.
2. Eager compile `real_normalization.json`.
3. Gọi `retry_pending_sim_bundles(tenant_id, network_id)`.

Response của `PUT real-network` sẽ có field `retryPendingSimBundles`:

```json
{
  "retryPendingSimBundles": {
    "retried": 1,
    "succeeded": ["sim-cologne3-abc"],
    "failed": []
  }
}
```

Operator cũng có thể trigger retry thủ công qua `POST /ops/auto-sync/scan-now` (nó sẽ pickup lại URI và composer sẽ re-attempt).

## 4. Idempotency và race conditions

### 4.1 Dedup logic

Cả listener và poller cùng phát hiện 1 bundle → cần dedup:

1. **In-memory lock per URI** — `_handle_uri()` dùng set `_state.in_progress`. Nếu URI đang xử lý từ source khác (listener vs poller) → skip ngay.
2. **DB check** — `bundle_exists_by_source_uri(source_uri)` query DB. Nếu URI đã có ban ghi (any status: validated/active/rolled_back/rejected) → skip.
3. **Atomic file ops** — extract vào staging dir → `shutil.move` atomic vào target. Pull lại không corrupt.

### 4.2 Failure modes

| Tình huống | Behavior |
|-----------|----------|
| MinIO down | Listener log warning, exponential backoff reconnect |
| Bundle ZIP hỏng (checksum fail) | Reject, status=`rejected`, bundle cũ vẫn active |
| `topology_hash` mismatch | Reject, log error, bundle cũ active |
| **Sim bundle về trước real snapshot** | **Status=`pending_real_snapshot`, auto-retry khi snapshot upload** |
| **Sim bundle schema_version không hỗ trợ** | **Reject với message rõ ràng, không lưu DB** |
| Activate fail (DB lỗi) | Bundle ở status `validated`, có thể manual activate sau |
| Listener disconnect | Auto reconnect (5s → 10s → 20s → 60s max) |
| ai-ops restart | Listener mất nhưng poller (10 phút) sẽ pickup khi restart xong |

### 4.3 Prefix safety check

Khi service start với cấu hình `SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true` + `SIM_BUNDLE_UPLOAD_RUNTIME=true`, runtime bundle composer build xong sẽ được upload trở lại MinIO để audit. Nếu prefix runtime overlap với prefix sim, listener sẽ pickup runtime bundle vừa upload → vòng lặp compose vô tận.

`_check_prefix_safety()` chạy lúc khởi động sẽ log warning rõ ràng nếu phát hiện:

- `sim_bundle_prefix` và `artifact_bundle_prefix` trùng hoặc lồng nhau.
- `sim_bundle_suffix='.zip'` (quá generic).

**Production khuyến nghị:**

```bash
SIM_BUNDLE_PREFIX=sim/
SIM_BUNDLE_SUFFIX=.sim.zip
ARTIFACT_BUNDLE_PREFIX=runtime/
```

Lần đầu service start, kiểm tra log:

```
[auto-sync] Started. prefix=sim/ suffix=.sim.zip poll_interval=600s
```

Nếu thấy `[auto-sync] CANH BAO: ... overlap ...` thì điều chỉnh env vars trước khi dùng production.

## 5. Monitoring

### 5.1 Status endpoint

```powershell
$h = @{ "X-Internal-API-Key" = "<key>" }
Invoke-RestMethod -Uri http://localhost:8002/ops/auto-sync/status -Headers $h
```

Response:
```json
{
  "enabled": true,
  "started_at": 1715000000.0,
  "listener": {
    "alive": true,
    "reconnects": 0,
    "last_event_at": 1715001234.5
  },
  "poller": {
    "alive": true,
    "runs": 12,
    "last_run_at": 1715002800.0
  },
  "in_progress": [],
  "pulled_count": 5,
  "failed_count": 0,
  "last_error": null
}
```

### 5.2 Force scan (debug)

Trigger 1 lần scan ngay không đợi poller interval:
```powershell
Invoke-RestMethod -Method POST `
  -Uri http://localhost:8002/ops/auto-sync/scan-now -Headers $h
# {"scanned": 5, "pulled": ["s3://..."]}
```

### 5.3 Bundle lifecycle audit

Xem lịch sử thao tác cho 1 bundle:
```powershell
Invoke-RestMethod -Uri "http://localhost:8002/ops/bundles/<bundle_id>/events" -Headers $h
```

Mỗi sự kiện (pull/validate/activate/rollback) ghi vào `bundle_event` table với:
- `actor` — `auto-sync-listener` / `auto-sync-poller` / `manual-scan` / `ai-ops` (manual API)
- `status` — `ok` / `failed` / `rejected`
- `detail` — context cụ thể

## 6. Bật / tắt auto-sync runtime

Auto-sync khởi tạo trong FastAPI lifespan startup (xem [src/main.py](../src/main.py)). Để bật/tắt:

```powershell
# Sửa env trong docker-compose.yml hoặc .env
MINIO_AUTO_SYNC_ENABLED=true   # hoặc false

# Restart container
docker compose restart ai-ops
```

Verify:
```powershell
Invoke-RestMethod -Uri http://localhost:8002/ops/auto-sync/status -Headers $h | Select-Object enabled
# enabled: true
```

## 7. Troubleshooting

### Listener không alive

```powershell
$h = @{ "X-Internal-API-Key" = "<key>" }
$status = Invoke-RestMethod -Uri http://localhost:8002/ops/auto-sync/status -Headers $h
$status.listener
```

Nếu `alive: false` và `reconnects > 0`:
- Check `last_error` field
- Verify MinIO endpoint reachable từ container: `docker exec ai_ops curl http://minio:9000/minio/health/live`
- Verify credentials: `docker exec ai_ops env | grep MINIO`

### Bundle uploaded nhưng không tự deploy

1. **Check listener đang alive:** `auto-sync/status`
2. **Check prefix khớp:** `MINIO_AUTO_SYNC_PREFIX` vs path upload thực tế
3. **Check suffix khớp:** mặc định `.zip`
4. **Force scan:** `POST /ops/auto-sync/scan-now`
5. **Check log:** `docker compose logs ai-ops | grep auto-sync`

### Pull fail liên tục

Check `bundle_event`:
```powershell
docker exec ai_ops uv run python -c "from src.db.base import get_session; from src.db.models import BundleEvent; from sqlalchemy import select, desc; with get_session() as s: events = s.scalars(select(BundleEvent).order_by(desc(BundleEvent.created_at)).limit(10)).all(); [print(f'{e.event_type} {e.status}: {e.detail}') for e in events]"
```

Common causes:
- Bundle ZIP thiếu file required (manifest, policy.onnx, ...)
- `topology_hash` trong manifest ≠ hash thực tế (network.json bị sửa sau khi build)
- File checksum mismatch (ZIP corrupt khi upload)

## 8. Source code

| File | Vai trò |
|------|---------|
| [src/ops/auto_sync.py](../src/ops/auto_sync.py) | Module chính: listener thread + poller async + status API |
| [src/services/artifact_storage.py](../src/services/artifact_storage.py) | `list_remote_zips()`, `listen_remote_zips()` helpers |
| [src/ops/lifecycle.py](../src/ops/lifecycle.py) | `pull_and_register_bundle(auto_activate=True)` |
| [src/db/repositories.py](../src/db/repositories.py) | `bundle_exists_by_source_uri()` dedup helper |
| [src/main.py](../src/main.py) | Lifespan hook khởi động auto-sync khi role=ops/all |

## 9. Bước tiếp theo

- [demo-quickstart.md](demo-quickstart.md#5-upload-bundle--service-tự-deploy) — demo auto-sync
- [deployment.md](deployment.md) — context production vendor cloud + customer edge
- [troubleshooting.md](troubleshooting.md) — debug common issues
