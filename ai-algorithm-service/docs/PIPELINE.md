# Pipeline Sim → Real (End-to-End)

> **Đối tượng**: kỹ sư mới onboard, DevOps cần deploy, hoặc team training cần biết phía service đang chờ artifact gì.
> Tài liệu này mô tả **chính xác** luồng dữ liệu và artifact trong dự án — code đã được refactor để khớp với pipeline mô tả ở đây.

---

## 1. Tổng quan pipeline

```
[Team Sim Training]                              [Team Service / Ops]
───────────────────                              ─────────────────────
1) Training trên mô phỏng                        3) Đăng ký mạng lưới thực
   policy.onnx                                       ├─ controller gửi: area + crosses
   policy_meta.json                                  │  + stages + roads + cycles
   sim_network.json                                  ├─ service lưu vào DB nội bộ
       │                                             │  (real_network_snapshot)
       ▼                                             ├─ service tự compile
2) Đóng gói Sim Bundle (.sim.zip)                    │  real_normalization.json (ngay!)
   + sim_bundle_manifest.json                        └─ trả về area_id + checksum
   (chứa: network_id, tenant_id,
    schema_version, version)                       
       │                                             
       ▼                                             
   Upload → MinIO (prefix: sim/)                    
       │
       │ Listener long-poll (1-2s)
       ▼
                    ┌─────────────────────────────┐
                    │ 4) ai-ops Auto Sync         │
                    │    ├─ pull Sim Bundle       │
                    │    ├─ validate schema_ver   │
                    │    ├─ tìm real_normalization│
                    │    │  trong DB              │
                    │    │  (nếu thiếu → status   │
                    │    │   pending_real_snapshot│
                    │    │   chờ retry)           │
                    │    ├─ build deployment_map  │
                    │    ├─ validate compatibility│
                    │    ├─ build Runtime Bundle  │
                    │    └─ activate (auto)       │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │ 5) ai-runtime Inference     │
                    │    POST /api/algorithm/ai   │
                    │    ├─ readiness guard       │
                    │    ├─ load Runtime Bundle   │
                    │    ├─ ONNX inference        │
                    │    └─ response              │
                    └─────────────────────────────┘
```

---

## 2. Phân vai trò 2 container

| Container | Vai trò | Port mặc định | Routes chính |
|---|---|---|---|
| `ai-ops` | Quản lý bundle, sync, compose | 8002 | `/internal/sync/*`, `/ops/*` |
| `ai-runtime` | Inference + readiness | 8001 | `/api/algorithm/ai`, `/health`, `/ready` |

Cùng image, share Local Model Storage qua volume. Process role chọn qua env `SERVICE_ROLE=runtime|ops|all`. Trong dev có thể chạy `all` để 1 process serve cả 2.

---

## 3. Bước 1 — Training team build Sim Bundle

### 3.1 Artifact yêu cầu

Sau khi training xong, team training có 3 file:

| File | Vai trò | Bắt buộc |
|---|---|---|
| `policy.onnx` | Weights ONNX | ✅ |
| `policy_meta.json` | hyperparameters, obs_stats, input/output names | ✅ |
| `sim_network.json` | Sim normalization — cấu trúc mạng lưới mô phỏng đã training | ✅ |

> ⚠️ Tên cũ là `intersection_config.json` (legacy). Service vẫn đọc được nhưng manifest mới phải dùng `sim_network.json`.

### 3.2 Đóng gói Sim Bundle

> 📌 **Về vị trí script `build_sim_bundle.py`**
>
> Script này hiện nằm **trong repo training `Service-ai`** (tại `Service-ai/scripts/build_sim_bundle.py`), KHÔNG còn trong repo service. Lý do:
>
> - Việc đóng gói bundle là bước cuối của pipeline huấn luyện, output (`policy.onnx`, `policy_meta.json`, `sim_network.json` lấy từ `intersection_config.json`) đều sinh ở Service-ai.
> - Service chỉ cần đọc/validate bundle khi nhận từ MinIO — phần đó nằm ở `src/ops/sim_bundle.py` (reader + `SimBundleManifest` schema).
>
> Khi cần bump `schema_version`, cả 2 repo phải đồng bộ: cập nhật `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` trong `src/ops/sim_bundle.py` (service) và `SIM_BUNDLE_SCHEMA_VERSION` trong `Service-ai/scripts/build_sim_bundle.py` (training).

Cách chạy script (bên repo Service-ai):

```bash
cd Service-ai
python scripts/build_sim_bundle.py \
  --tenant-id default \
  --network-id cologne3 \
  --version v2026.05.15 \
  --sim-network path/to/intersection_config.json \
  --policy-onnx path/to/policy.onnx \
  --policy-meta path/to/policy_meta.json \
  --output-zip dist/cologne3-v2026.05.15.sim.zip
```

Output `*.sim.zip` chứa:

```
cologne3-v2026.05.15.sim.zip
├── sim_bundle_manifest.json   ← schema_version=1, network_id, tenant_id, version, ...
├── policy.onnx
├── policy_meta.json
└── sim_network.json
```

### 3.3 `sim_bundle_manifest.json` ví dụ

```json
{
  "schema_version": 1,
  "sim_bundle_id": "sim-cologne3-a1b2c3d4",
  "tenant_id": "default",
  "network_id": "cologne3",
  "version": "v2026.05.15",
  "sim_network_path": "sim_network.json",
  "policy_onnx_path": "policy.onnx",
  "policy_meta_path": "policy_meta.json",
  "training_run_id": "mlflow-run-xyz",
  "training_dataset_id": "dataset-cologne3-2026q2",
  "training_pipeline_commit": "git-sha-abc1234"
}
```

### 3.4 Schema version enforcement

Service **chỉ chấp nhận** `schema_version ∈ {1}` (xem `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` trong [src/ops/sim_bundle.py](../src/ops/sim_bundle.py)). Khi bump schema, training team phải đảm bảo service đã upgrade trước. Bundle outdated → fail rõ ràng với message hướng dẫn.

### 3.5 Upload lên MinIO

Bucket pattern khuyến nghị:

```
s3://ai-models/sim/{tenant_id}/{network_id}/{network_id}-{version}.sim.zip
```

- **Prefix `sim/`**: listener auto-sync filter theo prefix này.
- **Suffix `.sim.zip`**: phân biệt với runtime bundle (suffix `.zip` thường).

Trong production, pipeline CI/CD bên repo `Service-ai` (training) đảm nhận việc push `.sim.zip` lên MinIO sau khi build xong. Jenkinsfile của repo service KHÔNG còn build / push sim bundle nữa.

---

## 4. Bước 2 — Service team đăng ký mạng lưới thực tế (song song với bước 1)

### 4.1 Tại sao cần song song

Pipeline được thiết kế để 2 luồng (training và đăng ký area) chạy độc lập:

- Training team không cần đợi service đăng ký area xong mới upload sim bundle.
- Service có thể đăng ký area trước khi có bất kỳ sim bundle nào.

Service xử lý race-condition bằng cơ chế `pending_real_snapshot` (xem mục 6.2).

### 4.2 Endpoint đăng ký

```http
PUT /internal/sync/areas/{area_id}/real-network
Headers:
  X-Internal-API-Key: <secret>
  Content-Type: application/json
```

**Payload tối thiểu:**

```json
{
  "sourceEventId": "real-network-1-cologne3-20260521",
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "sourceVersion": "management-area-1308556",
  "area": { "area_id": 1, "area_name": "Cologne 3 intersections" },
  "areaCrosses": [
    { "area_id": 1, "cross_id": 101, "cycle_id": 5001, "is_active": 1 }
  ],
  "crosses": [
    { "id": 101, "cross_name": "Cross A", "location": "21.027,105.829" }
  ],
  "roads": [
    {
      "id": 7001, "from_cross": 101, "to_cross": 102,
      "from_cross_direction": 1, "to_cross_direction": 3,
      "number_of_lanes": 3, "length": 250, "speed_design": 50,
      "coordinates": [
        { "order_number": 1, "latitude": 21.027, "longitude": 105.829 },
        { "order_number": 2, "latitude": 21.029, "longitude": 105.829 }
      ]
    }
  ],
  "cycles": [
    { "id": 5001, "cross_id": 101, "cycle_type": 0 }
  ],
  "stages": [
    { "id": 9001, "cycle_id": 5001, "order_number": 1 },
    { "id": 9002, "cycle_id": 5001, "order_number": 2 }
  ],
  "simToReal": {
    "0": 101,
    "1": 102
  }
}
```

> Payload mẫu đầy đủ (5 cross + 19 road + polyline đúng layout `v_road_coordinate`): xem [dist/full_real_network_snapshot.example.json](../dist/full_real_network_snapshot.example.json).

### 4.3 Điều gì xảy ra phía service

Khi nhận request `PUT /internal/sync/areas/{area_id}/real-network`, service làm **đồng thời** các việc sau:

1. **Idempotency check** qua `sourceEventId`. Trùng → return `{"status": "duplicate"}`.
2. **Validate payload** (đảm bảo có `areaCrosses`, `crosses`, `cycles`, `stages`).
3. **Upsert** `area_registry` (tạo nếu chưa có, gắn `tenant_id` + `network_id`).
4. **Lưu snapshot** vào bảng `real_network_snapshot` (payload_json + checksum).
5. **Eager compile `real_normalization.json`** → ghi vào `{model_dir}/real_normalization/area_{area_id}/`.
6. **Retry compose** cho sim bundle đang `pending_real_snapshot` cho `(tenant_id, network_id)` này.

### 4.4 Response mẫu

```json
{
  "status": "applied",
  "areaId": 1,
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "checksum": "ab12cd34...",
  "counts": {
    "areaCrosses": 3, "crosses": 3, "roads": 8, "cycles": 3, "stages": 9
  },
  "realNormalization": {
    "status": "ok",
    "outputDir": "/app/models/real_normalization/area_1"
  },
  "retryPendingSimBundles": {
    "retried": 1,
    "succeeded": ["sim-cologne3-a1b2c3d4"],
    "failed": []
  }
}
```

### 4.5 Verify trước khi training upload sim bundle

Controller hoặc operator có thể xem file chuẩn hoá đã compile:

```http
GET /internal/sync/areas/{area_id}/real-normalization
Headers: X-Internal-API-Key: <secret>
```

Trả về `{"areaId", "path", "content": { ... real_normalization.json ... }}`.

Nếu cần compile lại (vì sửa data thô trong DB, hoặc upgrade logic chuẩn hoá):

```http
POST /internal/sync/areas/{area_id}/real-normalization/recompile
```

### 4.6 Direction inference (GPS-first, legacy-fallback)

Service phải gán mỗi `road` của một cross vào đúng 1 trong 4 hướng chuẩn `{N, E, S, W}`. Đây là invariant policy đã học từ mô phỏng (sim GPI ở [Service-ai/src/preprocessing/standardizer.py](../../Service-ai/src/preprocessing/standardizer.py)), nên `real_normalization` bắt buộc reproduce cùng cách bucket.

[src/ops/real_normalization.py](../src/ops/real_normalization.py) hỗ trợ 3 cách lấy hướng, theo thứ tự ưu tiên:

**1. GPS-driven (khuyến nghị production):**

```text
cross center (v_cross.location hoặc cross.center_coordinate)
    +
road polyline (v_road_coordinate hoặc road.coordinates, sort theo order_number)
    │
    ▼
endpoint gần cross center  =  "junction stop line"
endpoint còn lại            =  "approach far point"
    │
    ▼
vector INTO junction  =  stop_line − previous_point   (trong ENU phẳng quanh cross)
angle                 =  atan2(dy_north, dx_east) % 360
    │
    ▼
N: 225° ≤ angle < 315°
E: 135° ≤ angle < 225°
S:  45° ≤ angle < 135°
W: else
```

Quy tắc bucket trùng đúng với [Service-ai standardizer.py:149](../../Service-ai/src/preprocessing/standardizer.py). Khi nhiều road cùng bucket, chọn road có `angle` gần nhất với góc lý tưởng (N=270°, E=180°, S=90°, W=0°) — y hệt tiebreaker GPI sim.

**2. Legacy `from_cross_direction` / `to_cross_direction`:**

Khi cross không có GPS, service đọc cột số nguyên `from_cross_direction` (và `to_cross_direction` cho internal road). Service **auto-detect encoding mỗi snapshot**:

| Encoding | Khi nào trigger | Bảng map |
|---|---|---|
| 4-direction | Toàn bộ giá trị direction code đều nằm trong `{1, 2, 3, 4}` | `1=N, 2=E, 3=S, 4=W` |
| 8-direction | Có ít nhất một giá trị ngoài `{1, 2, 3, 4}` (vd `0`, `6`, hoặc diagonal `5`) | `0=N, 2=E, 4=S, 6=W` (`1/3/5/7` là NE/SE/SW/NW — không thể bucket vào cardinal, sẽ bị drop) |

Detector code: `_detect_legacy_direction_encoding()` trong [real_normalization.py](../src/ops/real_normalization.py).

**3. Không có cả GPS lẫn legacy code → drop road đó:**

Service KHÔNG fallback round-robin (cách cũ đã bỏ). Cross thiếu hoàn toàn data hướng → `direction_map` rỗng → composer raise `ComposeError` rõ ràng. Triết lý: fail loud thay vì silent-misroute observation channel.

**Diagonal code (NE/SE/SW/NW) trong 8-direction:**

Code `1, 3, 5, 7` trong 8-dir là diagonals. Service intentionally drop chúng. Nếu DB customer dùng diagonal cho road thật (hiếm — đa số intersection 4-way là cardinal), operator phải bổ sung polyline để GPS-driven path cover.

**Hệ quả với composer:**

[composer._real_road_by_direction](../src/ops/composer.py) giờ pick `min(road_ids)` khi nhiều road cùng direction (deterministic). Vì `_build_direction_map` đã enforce tối đa 1 road per direction qua tiebreaker GPI, edge case nhiều entry chỉ xảy ra với snapshot legacy chưa recompile.

---

## 5. Bước 3 — Auto Sync + Compose (ai-ops)

### 5.1 Cơ chế auto-sync

`src/ops/auto_sync.py` chạy **2 thread song song**:

1. **Listener** (`listen_bucket_notification`): long-poll MinIO bucket. Latency ~1-2s khi có ObjectCreated event mới.
2. **Safety-net poller**: scan bucket mỗi 10 phút (config `MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS`). Bắt event bị miss khi listener disconnect.

Cả 2 đều gọi `_handle_uri()` → idempotent (skip URI đã pull trước đó, lock per-URI).

### 5.2 Flow compose runtime bundle

Khi listener phát hiện `*.sim.zip` mới:

```
1. Download .sim.zip về /tmp
2. Validate sim bundle:
   - File required (manifest, policy.onnx, policy_meta.json)
   - schema_version ∈ {1}
3. Resolve area_id từ (tenant_id, network_id) trong area_registry
   ├─ Nếu chưa có area / chưa có real_network_snapshot:
   │  → status='pending_real_snapshot', dừng tại đây
   │  → ghi BundleEvent 'compose-deferred'
   │  → ĐỢI snapshot upload, sẽ tự retry
   └─ Nếu có: tiếp tục
4. Compile real_normalization.json từ snapshot (nếu chưa có)
5. Build deployment_map.json từ sim_network + real_normalization
   - resolve sim_to_real mapping (explicit ưu tiên, fallback order-based)
   - validate compatibility:
     * SIM_CROSS_NOT_MAPPED
     * REAL_CROSS_NOT_FOUND
     * STAGE_COUNT_MISMATCH
     * DIRECTION_MISSING_IN_REAL
6. Build Runtime Bundle qua bundle-tooling v2:
   - policy.onnx + policy_meta.json
   - network.json + intersections/cross_*.json
   - feature_formula.json
7. Enrich Runtime Bundle:
   - thêm sim_network.json (audit)
   - thêm real_normalization.json (audit)
   - thêm compatibility_report.json (audit)
   - recompute checksum manifest
8. Register Runtime Bundle vào DB (model_bundle table)
9. Activate (nếu SIM_BUNDLE_AUTO_ACTIVATE=true)
   - write active.json
   - notify ai-runtime hot-reload qua HTTP
10. (Optional) Upload Runtime Bundle lên MinIO prefix khác (audit/redistribute)
```

### 5.3 Runtime Bundle layout

```
{bundle_id}.zip
├── policy.onnx
├── policy_meta.json
├── network.json                       ← cho ai-runtime dùng
├── intersections/
│   ├── cross_{real_id}.json
│   └── ...
├── feature_formula.json
├── deployment_map.json                ← internal, dùng cho validation
├── sim_network.json                   ← audit
├── real_normalization.json            ← audit
├── compatibility_report.json          ← audit
└── model_manifest.json                ← checksum tất cả file
```

Local layout sau khi extract:

```
{model_dir}/networks/{network_id}/
├── active.json                        ← ActivePointer
├── bundles/
│   └── {bundle_id}/                   ← bundle đã extract
└── archive/
    └── {bundle_id}.zip                ← bản zip gốc
```

### 5.4 Compatibility Gates

Bundle bị **reject** khi composer phát hiện:

| Gate | Khi nào fail |
|---|---|
| `SIM_CROSS_NOT_MAPPED` | Sim cross không có trong `sim_to_real` mapping và không match được order-based |
| `REAL_CROSS_NOT_FOUND` | Mapping trỏ tới real_cross_id không tồn tại trong snapshot |
| `STAGE_COUNT_MISMATCH` | Số phase sim ≠ số stage real trong primary cycle |
| `DIRECTION_MISSING_IN_REAL` | Sim có edge hướng N/E/S/W nhưng real không có road tương ứng (xem [§4.6](#46-direction-inference-gps-first-legacy-fallback) — direction được suy từ GPS hoặc legacy code) |
| `DEPLOYMENT_MAP_VALIDATION_EXCEPTION` | bundle-tooling validator raise |

`compatibility_report.json` trong runtime bundle giữ chi tiết errors + warnings.

Nếu `DIRECTION_MISSING_IN_REAL` xảy ra, thường vì cross thật thiếu road ở hướng đó hoặc cả GPS lẫn legacy code đều không xác định được direction. Kiểm tra log `[real_normalization]` để xem thông báo encoding nào được detect và cross nào thiếu center.

### 5.5 Warning `AUTO_CROSS_MAPPING_BY_ORDER`

Khi `real_normalization` không có `sim_to_real` mapping explicit và số cross sim == số cross real, composer auto-map theo thứ tự và ghi warning. **Production khuyến nghị luôn cung cấp `simToReal` explicit** trong payload `PUT /areas/{id}/real-network`.

---

## 6. Edge cases & xử lý lỗi

### 6.1 Sim bundle về trước real snapshot

- Bundle được lưu với `bundle_kind='sim'`, `status='pending_real_snapshot'`.
- BundleEvent `compose-deferred` được ghi với reason.
- Khi controller upload snapshot, `sync_real_network_snapshot` tự gọi `retry_pending_sim_bundles(tenant_id, network_id)` để retry.
- Operator có thể trigger retry manual qua `POST /ops/auto-sync/scan-now`.

### 6.2 Schema version mismatch

- Service log error rõ ràng + reject bundle.
- Bundle KHÔNG vào DB → khi training rebuild với schema đúng, không gây conflict.

### 6.3 Prefix sim / runtime overlap

`auto_sync.start()` chạy `_check_prefix_safety()` khi service khởi động:

- Cảnh báo nếu `sim_bundle_prefix` và `artifact_bundle_prefix` overlap.
- Cảnh báo nếu `sim_bundle_suffix='.zip'` (quá generic, nên dùng `.sim.zip`).
- Không raise — chỉ log để operator chú ý.

**Production khuyến nghị:**

```bash
SIM_BUNDLE_PREFIX=sim/
SIM_BUNDLE_SUFFIX=.sim.zip
ARTIFACT_BUNDLE_PREFIX=runtime/
```

### 6.4 Bundle đã pull nhưng chưa activate

`auto_activate=False` (override env `SIM_BUNDLE_AUTO_ACTIVATE=false`) → operator phải gọi manual:

```http
POST /ops/bundles/{bundle_id}/activate
```

### 6.5 Rollback

```http
POST /ops/networks/{network_id}/rollback
```

Quay về bundle gần nhất từng active. Service ghi BundleEvent `rollback` + `restore`.

---

## 7. Bước 4 — Inference (ai-runtime)

### 7.1 Endpoint

```http
POST /api/algorithm/ai
Content-Type: application/json
```

Payload: xem [api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

### 7.2 Readiness guard

Mỗi request kiểm tra `check_area(area_id)`:

- `area_registry` có và `is_active=True`.
- ActivePointer tồn tại + bundle dir + file required.

Nếu chưa ready → return `AREA_NOT_READY`.

### 7.3 Pipeline inference

```
1. Topology Normalizer
   - lane_features (48-dim) + green_time_ratios (8-dim) = 56-dim
   - z-score qua obs_stats
   - push observation_history (sliding window)
2. ONNX Inference
   - Local-GNN: self + neighbor features
   - Global-GNN: batch tất cả cross
3. Phase Normalizer (action mapper)
   - map 8 standard phases → stages thực
   - action delta: keep, +step, -step
4. Guardrails (Safety Layer)
   - min/max green clip
   - anti-starvation
   - rescale total green = cycle - yellow
5. Response: green-time per stage
```

### 7.4 Hot-reload bundle

Khi ai-ops activate bundle mới:

1. Ghi `active.json` mới (atomic).
2. Notify ai-runtime qua `POST /internal/runtime/reload` (nếu config `RUNTIME_INTERNAL_URL`).
3. ai-runtime invalidate cache + chạy preflight.

Nếu không có HTTP notify, ai-runtime tự poll `active.json` mỗi `ACTIVE_POINTER_TTL_SECONDS` giây.

---

## 8. Cheatsheet env vars

| Env | Mặc định | Mô tả |
|---|---|---|
| `SERVICE_ROLE` | `all` | `runtime` / `ops` / `all` |
| `MODEL_DIR` | `models` | Local Model Storage root |
| `MINIO_ENABLED` | `false` | Bật MinIO client |
| `MINIO_ENDPOINT` | — | `host:port` |
| `MINIO_BUCKET` | — | Bucket chứa bundle |
| `MINIO_AUTO_SYNC_ENABLED` | `false` | Bật listener + poller |
| `SIM_BUNDLE_AUTO_COMPOSE_ENABLED` | `false` | Bật auto-compose runtime từ sim bundle |
| `SIM_BUNDLE_PREFIX` | `""` | Prefix MinIO cho sim bundle (vd `sim/`) |
| `SIM_BUNDLE_SUFFIX` | `.sim.zip` | Suffix filter sim bundle |
| `SIM_BUNDLE_AUTO_ACTIVATE` | `true` | Auto-activate sau compose (production có thể đặt `false`) |
| `SIM_BUNDLE_UPLOAD_RUNTIME` | `true` | Upload runtime bundle lên MinIO để audit |
| `ARTIFACT_BUNDLE_PREFIX` | `bundles` | Prefix MinIO cho runtime bundle (phải KHÁC `SIM_BUNDLE_PREFIX`) |
| `MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS` | `600` | Safety-net poller interval |
| `INTERNAL_API_KEY` | — | Bắt buộc cho `/internal/sync/*` và `/ops/*` |
| `AI_STRICT_MODE` | `false` | Production = `true` để fail-fast khi area chưa ready |

Xem [docs/configuration.md](configuration.md) cho toàn bộ env vars.

---

## 9. Demo nhanh end-to-end (local)

```bash
# 1. Khởi động stack
docker compose --profile db --profile storage --profile app up -d

# 2. Đăng ký area + push real network snapshot
python scripts/register_real_network_snapshot.py \
  --db-url mysql+pymysql://root:123456@localhost:3306/statistic \
  --source-area-id 1308556 \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3

# 3. Verify real_normalization đã compile
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/internal/sync/areas/1/real-normalization

# 4. Build + upload sim bundle
# Chạy trong repo Service-ai (training repo)
cd ../Service-ai
python scripts/build_sim_bundle.py \
  --tenant-id default \
  --network-id cologne3 \
  --version v2026.05.15 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3-v2026.05.15.sim.zip
cd ../ai-algorithm-service

mc cp ../Service-ai/dist/cologne3-v2026.05.15.sim.zip \
  myminio/ai-models/sim/default/cologne3/cologne3-v2026.05.15.sim.zip

# 5. Đợi 2-3s, listener pickup. Verify bundle đã active:
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active

# 6. Inference
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -d @test_cologne3_payload.json
```

---

## 10. Sơ đồ data ownership

```
                            ┌─────────────────────────┐
                            │   real_network_snapshot │
                            │   (service-owned DB)    │
[Controller / Backend] ───→ │   - payload_json        │
                            │   - tenant_id           │
                            │   - network_id          │
                            │   - checksum            │
                            └────────────┬────────────┘
                                         │
                            (eager compile khi PUT)
                                         │
                                         ▼
                            ┌─────────────────────────┐
                            │  real_normalization.json│
                            │  + intersections/*.json │
                            │  + network.json         │
                            │  (file artifact)        │
                            └────────────┬────────────┘
                                         │
                                         │ (composer đọc khi build runtime bundle)
                                         │
[Training Team] ─ sim bundle ─→ MinIO ─→ │ ─→ Runtime Bundle ─→ MinIO (audit)
                                                  │
                                                  ▼
                                          {model_dir}/networks/.../
                                          + active.json
                                                  │
                                                  ▼
                                          [ai-runtime inference]
```

---

## 11. Đối chiếu với code

| Bước pipeline | File chính | Hàm |
|---|---|---|
| Build Sim Bundle | `Service-ai/scripts/build_sim_bundle.py` (repo training) | `main()` |
| Sim Bundle validate | [src/ops/sim_bundle.py](../src/ops/sim_bundle.py) | `validate_sim_bundle_dir()` |
| Auto-sync listener | [src/ops/auto_sync.py](../src/ops/auto_sync.py) | `_listener_loop()` |
| Register real network | [src/api/internal_sync.py](../src/api/internal_sync.py) | `sync_real_network_snapshot()` |
| Lưu snapshot + eager compile | [src/services/sync_service.py](../src/services/sync_service.py) | `sync_real_network_snapshot()` |
| Compile real normalization | [src/ops/real_normalization.py](../src/ops/real_normalization.py) | `compile_real_normalization()` |
| Direction inference (GPI) | [src/ops/real_normalization.py](../src/ops/real_normalization.py) | `_classify_road_at_cross()`, `_detect_legacy_direction_encoding()`, `_select_best_per_direction()` |
| Compose runtime bundle | [src/ops/composer.py](../src/ops/composer.py) | `compose_runtime_bundle_from_sim_zip()` |
| Retry pending sim bundle | [src/ops/lifecycle.py](../src/ops/lifecycle.py) | `retry_pending_sim_bundles()` |
| Activate bundle | [src/ops/lifecycle.py](../src/ops/lifecycle.py) | `activate_bundle()` |
| Inference | [src/services/ai_service.py](../src/services/ai_service.py) | `AIService.run()` |
| Readiness | [src/services/readiness_service.py](../src/services/readiness_service.py) | `check_area()` |

---

## 12. Câu hỏi thường gặp

**Q: Tại sao phải có cả `sim_network.json` và `real_normalization.json` trong runtime bundle?**
A: `sim_network.json` là contract training (giữ nguyên để audit, debug khi inference lệch). `real_normalization.json` là chuẩn hoá thực tế dùng cho inference. Composer build `deployment_map.json` để bridge 2 cái.

**Q: Có cần build runtime bundle ở CI/CD không?**
A: KHÔNG. Service tự compose runtime bundle khi nhận sim bundle. Việc build sim bundle thuộc về repo training `Service-ai` (`scripts/build_sim_bundle.py`), không phải Jenkinsfile của repo service.

**Q: Bundle bị `pending_real_snapshot` có tự xoá không?**
A: Không tự xoá. Operator có thể xoá bundle pending qua DB hoặc API admin (chưa expose). Khi controller upload snapshot, retry tự động.

**Q: 2 sim bundle khác version cho cùng `network_id` có conflict không?**
A: Không. `model_bundle` unique theo `(tenant_id, network_id, version)`. Bundle mới activate sẽ deactivate bundle cũ — bundle cũ giữ trạng thái `deprecated` để rollback.

**Q: Endpoint `/areas/{id}/artifacts` legacy có còn dùng được không?**
A: Còn, nhưng đã đánh dấu `deprecated=True`. Log warning mỗi lần gọi. Pipeline mới khuyến nghị dùng sim-bundle workflow.

---

## 13. Liên kết tham khảo

- [docs/sim-to-real-pipeline.md](sim-to-real-pipeline.md) — bản tóm tắt refactor
- [docs/architecture.md](architecture.md) — kiến trúc nội bộ
- [docs/auto-sync.md](auto-sync.md) — chi tiết listener/poller
- [docs/configuration.md](configuration.md) — env vars
- [docs/end-to-end-test.md](end-to-end-test.md) — test pipeline đầy đủ
- [api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) — API inference
