# API Reference

> Reference đầy đủ các endpoint của AI Algorithm Service. Service split 2 container: **ai-runtime** (port 8001) và **ai-ops** (port 8002), mỗi container mount 1 set router theo `SERVICE_ROLE`.
>
> 👉 Để hiểu pipeline tổng thể (Sim Bundle → Real Snapshot → Runtime Bundle), đọc [PIPELINE.md](PIPELINE.md). Nếu muốn chạy demo end-to-end: [end-to-end-test.md](end-to-end-test.md).

## 1. Tổng quan

| Container | Port | Phục vụ | Auth |
|-----------|------|---------|------|
| ai-runtime | 8001 | Public inference + readiness + internal hot-reload | Public + internal API key |
| ai-ops | 8002 | Bundle lifecycle + sync + auto-sync admin | Internal API key |

Khi `SERVICE_ROLE=all`, cả 2 router mount trong cùng 1 process (dev mode).

**Auth:** Endpoints `/internal/*` và `/ops/*` yêu cầu header `X-Internal-API-Key: <key>`. Public `/api/algorithm/ai/*` và `/health`, `/metrics` không cần.

**Swagger UI:** http://localhost:8001/docs và http://localhost:8002/docs.

## 2. Health & probes (cả 2 container)

### `GET /health`

Liveness probe.

**Response:** `{"status": "ok", "role": "runtime|ops|all"}`

### `GET /ready`

Readiness probe (200 nếu có ≥1 area sẵn sàng, 503 nếu không).

**Response 200:**
```json
{ "ready": true, "totalAreas": 3, "readyAreas": 3, "invalidAreas": [], "role": "runtime" }
```

**Response 503:** giống nhưng `ready: false` và `invalidAreas: [<id>...]`.

### `GET /metrics`

Prometheus metrics (text format). Counter, histogram, gauge — xem [observability.md](#) hoặc [src/observability/metrics.py](../src/observability/metrics.py).

## 3. ai-runtime endpoints (port 8001)

### 3.1 Public inference

#### `GET /api/algorithm/ai/areas`

List active + visible + ready areas (manifest cho Lớp 1 UI).

**Response:**
```json
{ "areas": [{ "areaId": 1, "areaName": "...", "policyVersion": "1.0.0", ... }] }
```

#### `GET /api/algorithm/ai/areas/{area_id}/readiness`

Detailed readiness của 1 area.

**Response:**
```json
{
  "areaId": 1,
  "ready": true,
  "hasPolicy": true,
  "hasMeta": true,
  "hasNetwork": true,
  "policyVersion": "1.0.0",
  "configVersion": "1",
  "activeArtifactId": 1,
  "missing": []
}
```

#### `GET /api/algorithm/ai/areas/{area_id}/network`

Trả `network.json` đang dùng cho area.

#### `GET /api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config`

Trả config cross (observation_mask, phase_mapping).

#### `PUT /api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config`

Override config cross (manual). Auth: không yêu cầu (dev tool).

#### `POST /api/algorithm/ai`

**Endpoint chính** — Core Controller gọi để inference. Chi tiết schema: [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

**Ghi chú dữ liệu đầu vào (roads):**
- Có thể gửi thêm `totalVehicle`, `windowSeconds`, `averageSpeedUnit`, `queueLength`, `density` để runtime tính/chuẩn hoá gần với mô phỏng.
- Nếu không gửi, runtime fallback theo bundle `feature_formula.json`.

**Request:**
```json
{
  "crosses": [
    {
      "id": 1,
      "areaId": 1,
      "type": 1,
      "cycle": { "id": 1, "cycleLength": 90, ... },
      "stages": [{ "id": 1, "duration": 47, ... }, ...],
      "roads": [{ "id": 1, "direction": 1, ... }, ...]
    }
  ],
  "cycleTime": 90,
  "yellowTime": 3,
  "minGreen": 10,
  "maxGreen": 60,
  "greenTimeStep": 5
}
```

**Response 200:**
```json
{
  "status": 1,
  "numIntersections": 1,
  "areaIds": [1],
  "algorithmOutputs": [
    {
      "cycleLength": 90,
      "crossId": 1,
      "areaId": 1,
      "phases": [
        { "stageId": 1, "stageCode": "P1", "greenTime": 47, "yellowTime": 3, "redClearTime": 1 },
        ...
      ]
    }
  ]
}
```

**Errors:**
- `400 INVALID_INPUT` — body schema sai
- `400 MULTIPLE_AREAS_NOT_ALLOWED` — request gửi >1 area (theo `enforce_single_area_per_request=true`)
- `404 AREA_NOT_FOUND` — areaId chưa register
- `409 AREA_NOT_READY` — area chưa có artifact / bundle / file
- `404 POLICY_NOT_FOUND` — bundle có ban ghi nhưng `policy.onnx` thiếu trên disk
- `500 INTERNAL_ERROR` — exception khác

#### `POST /api/algorithm/ai/cache/clear`

Force reload policy cache (dev tool).

**Body:** `{}` hoặc `{"areaId": 1}` để clear chỉ 1 area.

### 3.2 ai-runtime internal (cần API key)

Auth: header `X-Internal-API-Key: <runtime_key>`.

#### `POST /internal/runtime/reload`

Hot-reload bundle Active mới. ai-ops gọi sau khi activate (best-effort).

**Body:**
```json
{ "network_id": "cologne3", "runPreflight": true }
```

**Response:**
```json
{
  "status": "reloaded",
  "networkId": "cologne3",
  "preflight": "ok",
  "bundleId": "cologne3-v2026.05.15-abc",
  "version": "v2026.05.15"
}
```

**Errors:**
- `409 AREA_NOT_READY` — preflight fail (file thiếu hoặc topology_hash mismatch)

#### `GET /internal/runtime/active/{network_id}`

Đọc `active.json` của 1 network.

**Response:**
```json
{
  "bundle_id": "cologne3-v2026.05.15-abc",
  "version": "v2026.05.15",
  "topology_hash": "...",
  "previous_bundle_id": null,
  "activated_at": "2026-05-08T..."
}
```

#### `GET /internal/runtime/starvation`

Snapshot anti-starvation counter (debug guardrails).

**Response:** `{ "counts": { "1:0": 2, "1:1": 0, ... } }` (key = `cross_id:stage_idx`).

#### `GET /internal/runtime/drift`

Snapshot DriftDetector cho mọi network.

**Response:**
```json
{
  "detectors": {
    "cologne3": {
      "bundle_id": "cologne3-v2026.05.15-abc",
      "baseline_features": ["obs_mean"],
      "baseline_sizes": { "obs_mean": 200 },
      "window_sizes": { "obs_mean": 87 },
      "warmup_sizes": {},
      "counter": 87
    }
  }
}
```

## 4. ai-ops endpoints (port 8002)

Auth: header `X-Internal-API-Key: <ops_key>`.

### 4.1 Sync API

#### `PUT /internal/sync/areas/{area_id}`

Upsert area metadata. Với pipeline Sim Bundle mới, endpoint này vẫn hữu ích nhưng `PUT /internal/sync/areas/{area_id}/real-network` có thể tự upsert area luôn.

**Body:**
```json
{
  "sourceEventId": "evt-area-1-001",
  "areaName": "Demo Area",
  "isActive": true,
  "controllerVisible": true,
  "tenantId": "default",
  "networkId": "area_1"
}
```

**Response:** `{ "status": "applied|duplicate", "areaId": 1 }`

#### `PUT /internal/sync/areas/{area_id}/real-network`

Upsert snapshot mạng lưới thực tế vào DB nội bộ của AI service. Đây là **endpoint chính** cho pipeline Sim → Real.

Service thực hiện đồng thời:
1. Lưu snapshot vào `real_network_snapshot` table.
2. **Eager compile** `real_normalization.json` ngay (không đợi sim bundle).
3. Tự động **retry compose** cho sim bundle ở status `pending_real_snapshot` cho `(tenantId, networkId)` này.

**Body rút gọn:**
```json
{
  "sourceEventId": "evt-real-network-cologne3-001",
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "sourceVersion": "control-service-export-2026-05-20",
  "area": {},
  "areaCrosses": [],
  "crosses": [],
  "roads": [],
  "cycles": [],
  "stages": [],
  "simToReal": {
    "33202549": 567001
  }
}
```

Các list tương ứng với `v_area`, `v_area_cross`, `v_cross`, `v_road`, `v_cycle`, `v_stage` trong `management.sql`. Schema chi tiết: [PIPELINE.md §4.2](PIPELINE.md).

**Response:**
```json
{
  "status": "applied",
  "areaId": 1,
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "counts": {
    "areaCrosses": 5, "crosses": 5, "roads": 18, "cycles": 5, "stages": 15
  },
  "realNormalization": {
    "status": "ok",
    "outputDir": "/app/models/real_normalization/area_1"
  },
  "retryPendingSimBundles": {
    "retried": 0,
    "succeeded": [],
    "failed": []
  }
}
```

#### `GET /internal/sync/areas/{area_id}/real-normalization`

Xem nội dung `real_normalization.json` đã compile cho area.

**Response:**
```json
{
  "areaId": 1,
  "path": "/app/models/real_normalization/area_1/real_normalization.json",
  "content": {
    "area_id": 1,
    "network_id": "cologne3",
    "tenant_id": "default",
    "source": "service_snapshot",
    "generated_at": "...",
    "sim_to_real": { ... },
    "crosses": [ ... ]
  }
}
```

**Errors:** `404 CONFIG_NOT_FOUND` nếu chưa có snapshot.

#### `POST /internal/sync/areas/{area_id}/real-normalization/recompile`

Recompile `real_normalization.json` từ snapshot hiện có trong DB. Dùng khi:
- Sửa data thô trong DB ngoài luồng sync thông thường.
- Upgrade logic chuẩn hoá ở service.

Idempotent.

**Response:**
```json
{
  "status": "recompiled",
  "areaId": 1,
  "outputDir": "/app/models/real_normalization/area_1"
}
```

#### `PUT /internal/sync/areas/{area_id}/artifacts` ⚠️ DEPRECATED

> **DEPRECATED** — Endpoint legacy. Pipeline mới dùng sim bundle workflow. Service log warning mỗi lần endpoint này được gọi.

Upsert artifact version (cho area chưa có bundle).

**Body:**
```json
{
  "sourceEventId": "evt-artifact-1-001",
  "policyVersion": "1.0.0",
  "configVersion": "1.0.0",
  "activate": true
}
```

**Response:** `{ "status": "applied", "areaId": 1, "artifactId": 1, "activated": true }`

#### `POST /internal/sync/areas/{area_id}/artifacts/{artifact_id}/activate` ⚠️ DEPRECATED

> **DEPRECATED** — Dùng `POST /ops/bundles/{bundle_id}/activate` thay vì.

Promote artifact (manual swap).

#### `PUT /internal/sync/areas/{area_id}/crosses/{cross_id}/config`

Sync config cross (observation_mask, phase_mapping).

**Body:**
```json
{
  "sourceEventId": "evt-cross-101-001",
  "config": {
    "area_id": 1,
    "observation_mask": [1,1,1,1,1,1,0,0,0,0,0,0],
    "phase_mapping": { "0": 0, "1": 2, "2": 4, "3": 6, "4": -1, "5": -1, "6": -1, "7": -1 }
  }
}
```

#### `POST /internal/sync/finalize`

Validate readiness của các area, trả report.

**Body:** `{ "sourceEventId": "evt-finalize-001", "areaIds": [1, 2] }` (areaIds rỗng = tất cả active)

**Response:**
```json
{
  "status": "finalized|incomplete",
  "areas": [{ "areaId": 1, "ready": true, "missing": [] }]
}
```

### 4.2 Bundle lifecycle

#### `GET /ops/bundles`

List bundles. Query params (optional): `tenantId`, `networkId`, `status`, `bundleKind`.

**Status values:**
- `pulled`, `validated`, `active`, `deprecated`, `rolled_back`, `rejected` — runtime bundle lifecycle thông thường.
- `staged` — sim bundle đã pull, đang xử lý.
- `pending_real_snapshot` — sim bundle về trước khi có `real_network_snapshot` cho `(tenantId, networkId)`. Service tự retry compose khi snapshot được upload.
- `composed` — sim bundle đã được retry compose thành công, có runtime bundle con.

**Bundle kinds:**
- `sim` — Sim Bundle gốc từ training team.
- `runtime` — Runtime Bundle do composer sinh ra từ sim bundle + real snapshot.

**Response:**
```json
{
  "bundles": [
    {
      "bundleId": "area_1-1.0.0-abc",
      "bundleKind": "runtime",
      "parentBundleId": "sim-cologne3-xxxx",
      "tenantId": "default",
      "networkId": "area_1",
      "version": "1.0.0",
      "topologyHash": "...",
      "checksum": "...",
      "status": "active",
      "isActive": true,
      "sourceUri": "s3://...",
      "localPath": "/app/models/networks/area_1/bundles/area_1-1.0.0-abc",
      "activatedAt": "2026-05-08T..."
    }
  ]
}
```

#### `GET /ops/bundles/{bundle_id}`

Detail 1 bundle + manifest đầy đủ.

#### `POST /ops/bundles/pull`

Pull **runtime bundle** từ MinIO. **Có thể skip nếu đã bật auto-sync** ([auto-sync.md](auto-sync.md)).

**Body:**
```json
{
  "sourceUri": "s3://ai-models/bundles/default/cologne3/v2026.05.15/bundle.zip",
  "activate": true
}
```

**Response:** `{ "status": "validated|activated", "bundle": { ... } }`

#### `POST /ops/sim-bundles/pull`

Pull **Sim Bundle** từ MinIO → compose Runtime Bundle → activate (nếu `activate=true`).

**Body:**
```json
{
  "sourceUri": "s3://ai-models/sim/default/cologne3/cologne3.sim.zip",
  "activate": true
}
```

**Response:** `{ "status": "validated|activated", "bundle": { ... } }`

#### `POST /ops/bundles/register-local`

Register bundle đã có sẵn local path (dev/test).

**Body:** `{ "bundleDir": "/app/models/staging/x", "activate": true }`

#### `POST /ops/bundles/{bundle_id}/activate`

Activate bundle theo bundle_id.

**Response:** `{ "status": "activated", "bundle": { ... } }`

#### `POST /ops/networks/{network_id}/rollback`

Rollback về bundle Active trước đó.

**Body:** `{ "tenantId": "default" }` (hoặc `{}`)

**Response:** `{ "status": "rolled_back", "activeBundle": { ... } }`

#### `GET /ops/networks/{network_id}/active`

Đọc `active.json` của network.

**Response:**
```json
{
  "bundle_id": "area_1-1.0.0-abc",
  "version": "1.0.0",
  "topology_hash": "...",
  "previous_bundle_id": null,
  "activated_at": "..."
}
```

**Errors:** `409 AREA_NOT_READY` nếu chưa có active.

#### `GET /ops/bundles/{bundle_id}/events`

Audit events của bundle (pull, validate, activate, rollback, restore, register).

**Response:**
```json
{
  "bundleId": "area_1-1.0.0-abc",
  "events": [
    { "eventType": "pull", "status": "ok", "actor": "auto-sync-listener", "createdAt": "..." },
    { "eventType": "validate", "status": "ok", "actor": "auto-sync-listener", "createdAt": "..." },
    { "eventType": "activate", "status": "ok", "actor": "auto-sync-listener", "detail": "previous=null", "createdAt": "..." }
  ]
}
```

### 4.3 Auto-sync admin

#### `GET /ops/auto-sync/status`

Trạng thái listener + safety-net poller. Xem [auto-sync.md#monitoring](auto-sync.md#5-monitoring).

#### `POST /ops/auto-sync/scan-now`

Trigger 1 lần scan MinIO bucket ngay (không đợi poller interval).

**Response:** `{ "scanned": 5, "pulled": ["s3://..."] }`

## 5. Common error codes

[src/core/error_codes.py](../src/core/error_codes.py):

| Code | HTTP | Mô tả |
|------|------|-------|
| `INVALID_INPUT` | 400 | Body validation fail |
| `MULTIPLE_AREAS_NOT_ALLOWED` | 400 | Request có >1 area |
| `AREA_NOT_FOUND` | 404 | areaId chưa tồn tại |
| `AREA_NOT_READY` | 409 | area chưa đủ artifact / bundle / file |
| `POLICY_NOT_FOUND` | 404 | Bundle ghi DB nhưng policy.onnx mất |
| `CONFIG_NOT_FOUND` | 404 | network.json hoặc cross config mất |
| `SYNC_IDEMPOTENCY_CONFLICT` | 409 | sourceEventId đã có với payload khác |
| `UNAUTHORIZED` | 401 | API key sai/thiếu |
| `INTERNAL_ERROR` | 500 | Exception chưa handle |

**Format error chuẩn:**
```json
{
  "errorCode": "AREA_NOT_READY",
  "message": "Area 1 chua san sang: missing=['policy.onnx'].",
  "path": "/api/algorithm/ai",
  "requestId": "uuid",
  "areaId": 1,
  "missing": ["policy.onnx"]
}
```

## 6. Tham khảo

- [end-to-end-test.md](end-to-end-test.md#01-quick-demo-10-phut-skip-race-conditionrollback) — chạy thử nhanh
- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) — chi tiết `POST /api/algorithm/ai`
- [../postman/README.md](../postman/README.md) — Postman collection sẵn dùng
