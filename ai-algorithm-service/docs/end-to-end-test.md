# End-to-End Test: Sim Bundle → Real Network → Runtime Inference

> 🎯 **Đây là file chính để chạy demo từ đầu đến cuối**. Sau khi đọc xong file này, bạn sẽ chạy được toàn bộ pipeline trong ~15 phút.

> Nếu bạn chỉ muốn hiểu pipeline về mặt khái niệm trước, đọc [PIPELINE.md](PIPELINE.md). File hiện tại là phiên bản "hands-on" để **thực sự bấm chạy**.

## 0. Mục lục

| § | Nội dung | Thời gian |
|---|---|---|
| 0.1 | Quick demo (10 phut, skip race-condition/rollback) | 8-10 phut |
| 1 | Pre-requisites & cấu trúc workspace | 2 phút |
| 2 | Start Docker stack | 2 phút |
| 3 | Import Postman | 1 phút |
| 4 | Đăng ký Real Network Snapshot (đồng thời với bước 5) | 1 phút |
| 5 | Build + Upload Sim Bundle | 3 phút |
| 6 | Verify auto-compose + active bundle | 1 phút |
| 7 | Inference test | 1 phút |
| 8 | Test race-condition (Sim Bundle về trước Real Snapshot) | 3 phút |
| 9 | Rollback | 1 phút |
| 10 | Troubleshooting | — |

Cấu hình demo:

| Field | Value |
|---|---|
| `tenantId` | `default` |
| `networkId` | `cologne3` |
| `areaId` | `1` |
| Runtime URL | `http://localhost:8001` |
| Ops URL | `http://localhost:8002` |
| Internal API key | `sondinh2k3` |
| MinIO Console | `http://localhost:9001` |
| MinIO user/pass | `minioadmin` / `minioadmin` |

---

## 0.1 Quick demo (10 phut, skip race-condition/rollback)

Luồng rút gọn cho demo nhanh. Chi tiet tung buoc xem cac section 2, 4, 5, 6, 7 ben duoi.

```bash
cd ai-algorithm-service
uv lock
uv sync --extra dev
uv pip install -e ../traffic_rl_features -e ../bundle-tooling

docker compose --profile db --profile storage --profile app up -d --build

python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3

cd ../Service-ai
mkdir -p dist
python scripts/build_sim_bundle.py \
  --tenant-id default \
  --network-id cologne3 \
  --version v2026.05.15 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3.sim.zip

cd ../ai-algorithm-service
docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" \
  --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3.sim.zip local/ai-models/sim/default/cologne3/cologne3.sim.zip"

curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active

curl http://localhost:8001/api/algorithm/ai/areas/1/readiness

curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -d @test_cologne3_payload.json | python -m json.tool
```

Neu muon test bang Postman, import 2 file o muc 3 truoc khi goi API.

## 1. Pre-requisites & cấu trúc workspace
### 1.1 Yêu cầu

- Docker Desktop 24+ (hoặc Docker Engine + Compose v2).
- Python 3.11+, [`uv`](https://docs.astral.sh/uv/) để chạy script local.
- Postman (Desktop hoặc Web).

### 1.2 Cấu trúc thư mục

3 project **phải nằm ngang hàng** vì Dockerfile copy 2 sibling repo vào image:

```text
RL_algo_for_ITS/
├── ai-algorithm-service/      ← đang đứng ở đây
├── traffic_rl_features/
└── bundle-tooling/
```

Verify nhanh:

```bash
test -d ../traffic_rl_features && echo "OK: traffic_rl_features"
test -d ../bundle-tooling && echo "OK: bundle-tooling"
test -f docker-compose.yml && echo "OK: compose file"
```

Nếu thiếu một trong hai sibling repo, Docker build sẽ fail.

---

## 2. Start Docker stack

```bash
docker compose --profile db --profile storage --profile app up -d --build
docker compose ps
```

Container phải lên đủ 4:

| Container | Status mong đợi |
|---|---|
| `mysql_traffic` | `Up`, tốt nhất `healthy` |
| `minio_storage` | `Up` |
| `ai_runtime` | `Up` |
| `ai_ops` | `Up` |

Health check:

```bash
curl http://localhost:8001/health   # ai-runtime
curl http://localhost:8002/health   # ai-ops
```

Nếu container restart:

```bash
docker compose logs --tail 120 ai-runtime
docker compose logs --tail 120 ai-ops
```

→ Xem [troubleshooting.md §1](troubleshooting.md) nếu container không lên.

### 2.1 Kiểm tra safety check khi khởi động

Trong log `ai-ops` lần đầu khởi động bạn nên thấy:

```text
[auto-sync] Started. prefix=sim/default/ suffix=.sim.zip poll_interval=600s
```

Nếu thấy dòng `[auto-sync] CANH BAO: ... overlap ...` → kiểm tra config `SIM_BUNDLE_PREFIX` vs `ARTIFACT_BUNDLE_PREFIX` trong `docker-compose.yml` (phải khác nhau để tránh vòng lặp). Chi tiết: [auto-sync.md §4.3](auto-sync.md).

---

## 3. Import Postman

Import 2 file:

- [postman/RLOps E2E.postman_collection.json](../postman/RLOps%20E2E.postman_collection.json)
- [postman/RLOps Local.postman_environment.json](../postman/RLOps%20Local.postman_environment.json)

Chọn environment `RLOps Local` ở góc phải Postman.

Verify biến environment đã đúng:

| Variable | Value |
|---|---|
| `baseUrlRuntime` | `http://localhost:8001` |
| `baseUrlOps` | `http://localhost:8002` |
| `apiKeyOps` | `sondinh2k3` |
| `apiKeyRuntime` | `sondinh2k3` |
| `tenantId` | `default` |
| `networkId` | `cologne3` |
| `areaId` | `1308700` |

Chạy folder `A. Health & Runtime` — cả 3 request phải trả `200`.

---

## 4. Đăng ký Real Network Snapshot

**Đây là bước quan trọng nhất phía service**. Khi controller gọi endpoint này, service sẽ:

1. Lưu snapshot vào DB nội bộ (`real_network_snapshot` table).
2. **Eager compile** `real_normalization.json` ngay (không đợi sim bundle).
3. Nếu đã có sim bundle đang ở trạng thái `pending_real_snapshot` cho `(tenant_id, network_id)` này, service **tự động retry compose**.

### 4.1 Cách nhanh (dùng script Python)

```bash
python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3
```

Script này build payload Cologne3 sẵn và `PUT` lên endpoint. Output mong đợi:

```json
{
  "status": "applied",
  "areaId": 1,
  "tenantId": "default",
  "networkId": "cologne3",
  "counts": { "areaCrosses": 5, "crosses": 5, "roads": 18, "cycles": 5, "stages": 15 },
  "realNormalization": { "status": "ok", "outputDir": "/app/models/real_normalization/area_1" },
  "retryPendingSimBundles": { "retried": 0, "succeeded": [], "failed": [] }
}
```

### 4.2 Cách thủ công (qua Postman) — khi bạn muốn customize payload

Tạo request mới trong Postman:

```http
PUT {{baseUrlOps}}/internal/sync/areas/{{areaId}}/real-network
Headers:
  Content-Type: application/json
  X-Internal-API-Key: {{apiKeyOps}}
Body (raw, JSON):
```

Payload mẫu rút gọn dưới đây dùng encoding 4-direction (`from_cross_direction = 1..4`) — service tự auto-detect và chấp nhận. Payload đầy đủ với GPS polyline (khuyến nghị production, khử ambiguity direction encoding) xem [dist/full_real_network_snapshot.example.json](../dist/full_real_network_snapshot.example.json) hoặc generate qua `scripts/register_demo_real_network_snapshot.py`:

```json
{
  "sourceEventId": "evt-real-network-cologne3-001",
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "sourceVersion": "postman-demo-001",
  "area": { "AREA_ID": 1, "AREA_NAME": "Cologne3 Demo Area", "IS_ACTIVE": 1 },
  "areaCrosses": [
    { "area_id": 1, "cross_id": 567001, "cycle_id": 700001, "is_active": 1 },
    { "area_id": 1, "cross_id": 567002, "cycle_id": 700002, "is_active": 1 },
    { "area_id": 1, "cross_id": 567003, "cycle_id": 700003, "is_active": 1 },
    { "area_id": 1, "cross_id": 567004, "cycle_id": 700004, "is_active": 1 },
    { "area_id": 1, "cross_id": 567005, "cycle_id": 700005, "is_active": 1 }
  ],
  "crosses": [
    { "id": 567001, "is_active": 1, "cross_name": "Aachener_E" },
    { "id": 567002, "is_active": 1, "cross_name": "Side_S" },
    { "id": 567003, "is_active": 1, "cross_name": "Side_N" },
    { "id": 567004, "is_active": 1, "cross_name": "Aachener_Mid" },
    { "id": 567005, "is_active": 1, "cross_name": "Aachener_W" }
  ],
  "roads": [
    {
      "id": 100001, "is_active": 1, "road_name": "567001 N",
      "from_cross": 567001, "from_cross_direction": 1,
      "to_cross": null, "to_cross_direction": null,
      "number_of_lanes": 1, "length": 100, "speed_design": 50, "capacity_design": 1800
    }
  ],
  "cycles": [
    { "id": 700001, "is_active": 1, "cross_id": 567001, "cycle_type": 0,
      "number_of_stages": 2, "cycle_length": 90 }
  ],
  "stages": [
    { "id": 800001, "is_active": 1, "cycle_id": 700001, "order_number": 1,
      "stage_code": "P0", "green": 45, "yellow": 3, "red_clear": 1 }
  ],
  "simToReal": {
    "33202549": 567001,
    "360082": 567002,
    "360086": 567003,
    "360088": 567004,
    "cluster_2415878664_254486231_359566_359576": 567005
  }
}
```

> ⚠️ Khi gửi lại với payload sửa, đổi `sourceEventId` (idempotency key). Trùng `sourceEventId` + khác payload → `409 SYNC_IDEMPOTENCY_CONFLICT`.

> 📍 **GPS hay legacy direction code?** Payload trên dùng `from_cross_direction = 1..4` (encoding 4-direction) cho tiện gõ thủ công. Production khuyến nghị payload đầy đủ có `crosses[].location` + `roads[].coordinates`: service sẽ suy direction từ GPS qua thuật toán GPI giống Service-ai, an toàn hơn với mọi convention DB. Chi tiết [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback).

### 4.3 Verify chuẩn hoá thực tế đã sinh

Service có endpoint mới để xem trực tiếp file đã compile:

```http
GET {{baseUrlOps}}/internal/sync/areas/{{areaId}}/real-normalization
Headers: X-Internal-API-Key: {{apiKeyOps}}
```

Response mẫu:

```json
{
  "areaId": 1,
  "path": "/app/models/real_normalization/area_1/real_normalization.json",
  "content": {
    "area_id": 1,
    "network_id": "cologne3",
    "tenant_id": "default",
    "source": "service_snapshot",
    "generated_at": "2026-05-21T09:00:00+00:00",
    "sim_to_real": { ... },
    "crosses": [ ... ]
  }
}
```

`source` phải là `service_snapshot` (lấy từ DB nội bộ). Nếu là `management_views` → snapshot chưa được upload, kiểm tra lại bước 4.1/4.2.

### 4.4 Recompile khi cần

Nếu sửa data thô trong DB hoặc upgrade logic chuẩn hoá:

```http
POST {{baseUrlOps}}/internal/sync/areas/{{areaId}}/real-normalization/recompile
```

→ chạy lại `compile_real_normalization()` từ snapshot hiện có.

---

## 5. Build + Upload Sim Bundle

### 5.1 Build Sim Bundle

Sim Bundle là output từ training repo `Service-ai` (`policy.onnx + policy_meta.json + sim_network.json + sim_bundle_manifest.json`). Trong demo, build bằng script bên Service-ai:

```bash
# Chạy trong repo Service-ai
cd ../Service-ai
mkdir -p dist
python scripts/build_sim_bundle.py \
  --tenant-id default \
  --network-id cologne3 \
  --version v2026.05.15 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3.sim.zip
cd ../ai-algorithm-service
```

Output mong đợi: `[sim-bundle] OK id=sim-cologne3-XXXXXXXX output=.../Service-ai/dist/cologne3.sim.zip`

Inspect ZIP:

```bash
python -c "import zipfile; z=zipfile.ZipFile('../Service-ai/dist/cologne3.sim.zip'); print('\n'.join(sorted(z.namelist())))"
```

Phải có đủ 4 file:

```text
policy.onnx
policy_meta.json
sim_bundle_manifest.json
sim_network.json
```

### 5.2 Upload lên MinIO

```bash
docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" \
  --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3.sim.zip local/ai-models/sim/default/cologne3/cologne3.sim.zip"
```

Verify object trong MinIO console:

```text
http://localhost:9001
bucket: ai-models
object: sim/default/cologne3/cologne3.sim.zip
```

> 📌 **Lưu ý quan trọng**: Bundle phải nằm trong prefix `sim/default/` và đuôi `.sim.zip` để listener filter đúng. Đây là cấu hình mặc định trong `docker-compose.yml`:
> - `SIM_BUNDLE_PREFIX=sim/default/`
> - `SIM_BUNDLE_SUFFIX=.sim.zip`
> - `SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true`
> - `SIM_BUNDLE_AUTO_ACTIVATE=true`

---

## 6. Verify auto-compose + active bundle

Sau khi upload ~1-2 giây, listener auto-sync sẽ:

1. Pull `.sim.zip` về.
2. Validate `schema_version=1`.
3. Tìm `real_network_snapshot` cho `(default, cologne3)` — đã có ở bước 4.
4. Compile + build deployment_map + compatibility_report.
5. Build runtime bundle ZIP.
6. Register + activate.

### 6.1 Trigger scan thủ công (nếu muốn chắc chắn)

```http
POST {{baseUrlOps}}/ops/auto-sync/scan-now
```

Response mong đợi:

```json
{
  "scanned": 1,
  "pulled": ["s3://ai-models/sim/default/cologne3/cologne3.sim.zip"]
}
```

> Nếu `pulled` là `[]`, listener đã pickup từ trước rồi. Tiếp tục bước tiếp theo.

### 6.2 Xem active bundle

Trong Postman, chạy `C.2 Read active Cologne3 bundle`:

```http
GET {{baseUrlOps}}/ops/networks/cologne3/active
```

Response mong đợi:

```json
{
  "bundle_id": "cologne3-v2026.05.15-sim-cologne3-xxxxxxxx",
  "version": "v2026.05.15",
  "topology_hash": "...",
  "previous_bundle_id": null,
  "activated_at": "2026-05-21T..."
}
```

### 6.3 Check readiness

```bash
curl http://localhost:8001/api/algorithm/ai/areas/1308700/readiness
```

Expected:

```json
{
  "areaId": 1308700,
  "ready": true,
  "source": "bundle",
  "activeBundleId": "cologne3-v2026.05.15-..."
}
```

### 6.4 Xem log compose

```bash
docker compose logs --tail 200 ai-ops | grep -E "auto-sync|composer|real_normalization|Activated"
```

Mong đợi:

```text
[auto-sync] Pulling new bundle: s3://ai-models/sim/default/cologne3/cologne3.sim.zip
[real_normalization] built area=1 source=service_snapshot crosses=5 -> ...
[composer] Built runtime bundle ...
[ops] Activated bundle cologne3-v2026.05.15-... for network cologne3
```

---

## 7. Inference test

Trong Postman chạy `D.1 Inference - Cologne3 5 controlled crosses`:

```http
POST {{baseUrlRuntime}}/api/algorithm/ai
Content-Type: application/json
```

Body: dùng [test_cologne3_payload.json](../test_cologne3_payload.json).

Expected response:

| Field | Expected |
|---|---|
| HTTP status | `200` |
| `status` | `1` |
| `numIntersections` | `5` |
| `areaIds` | `[1]` |
| `algorithmOutputs` | 5 items |
| `algorithmOutputs[].crossId` | real cross IDs (`567001..567005`) |
| `algorithmOutputs[].phases[].stageId` | real stage IDs (`800001..800015`) |

Verify metrics:

```bash
curl http://localhost:8001/metrics | grep ai_inference
```

---

## 8. Test race-condition: Sim Bundle về TRƯỚC Real Snapshot

Đây là edge case quan trọng. Pipeline phải tự xử lý mà không lỗi vĩnh viễn.

### 8.1 Reset trạng thái (xoá active bundle + snapshot hiện tại)

```bash
docker compose down -v
docker compose --profile db --profile storage --profile app up -d
```

Verify chưa có gì:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active
# expected: 409 AREA_NOT_READY
```

### 8.2 Upload Sim Bundle TRƯỚC, KHÔNG gửi snapshot

```bash
# Build lại nếu cần — chạy trong repo training Service-ai
cd ../Service-ai
mkdir -p dist
python scripts/build_sim_bundle.py \
  --tenant-id default --network-id cologne3 --version v2026.05.15 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3.sim.zip
cd ../ai-algorithm-service

# Upload (vẫn chạy bên repo service vì cần network ai-algorithm-service_default)
docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3.sim.zip local/ai-models/sim/default/cologne3/cologne3.sim.zip"
```

### 8.3 Verify bundle ở status `pending_real_snapshot`

```bash
sleep 3  # đợi listener pickup
curl -H "X-Internal-API-Key: sondinh2k3" \
  "http://localhost:8002/ops/bundles?bundleKind=sim&status=pending_real_snapshot"
```

Expected:

```json
{
  "bundles": [
    {
      "bundleId": "sim-cologne3-xxxxxxxx",
      "bundleKind": "sim",
      "status": "pending_real_snapshot",
      "rejectedReason": "Area ... chua co real_network_snapshot. ...",
      "networkId": "cologne3"
    }
  ]
}
```

Log ai-ops:

```text
[ops] Sim bundle sim-cologne3-xxxxxxxx cho network=cologne3 cho real snapshot.
      Se retry tu dong khi controller upload snapshot.
```

### 8.4 Đăng ký snapshot → service TỰ ĐỘNG retry

```bash
python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3
```

Response sẽ có field `retryPendingSimBundles`:

```json
{
  "status": "applied",
  ...
  "retryPendingSimBundles": {
    "retried": 1,
    "succeeded": ["sim-cologne3-xxxxxxxx"],
    "failed": []
  }
}
```

### 8.5 Verify bundle giờ đã `active`

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active
# expected: active.json đầy đủ
```

→ Bundle pending đã tự compose lại và active. **Pipeline xử lý race-condition đúng**.

---

## 9. Rollback (quay về bundle version trước)

### 9.1 Build + upload version mới

```bash
cd ../Service-ai
python scripts/build_sim_bundle.py \
  --tenant-id default --network-id cologne3 --version v2026.05.16 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3-v2.sim.zip
cd ../ai-algorithm-service

docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3-v2.sim.zip local/ai-models/sim/default/cologne3/cologne3-v2.sim.zip"
```

Đợi 2-3s, verify version mới active:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active
# expected: version=v2026.05.16
```

### 9.2 Rollback về version cũ

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  -H "Content-Type: application/json" \
  -d '{"tenantId":"default"}' \
  http://localhost:8002/ops/networks/cologne3/rollback
```

Verify version cũ active lại:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active
# expected: version=v2026.05.15 (cũ)
```

---

## 10. Troubleshooting nhanh

| Triệu chứng | Cách xử lý |
|---|---|
| `C.1 scanned=0` | Sai bucket/path — verify trong MinIO console |
| `409 AREA_NOT_READY` | Sim bundle chưa compose → kiểm tra `GET /ops/bundles?status=pending_real_snapshot`, đăng ký snapshot ở bước 4 |
| `bundle status=pending_real_snapshot` | Đây là expected khi sim bundle về trước. Gửi snapshot → tự retry |
| `Sim bundle schema_version=X khong duoc ho tro` | Sim bundle build với schema khác — rebuild với phiên bản service hiện hỗ trợ (`SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` trong `src/ops/sim_bundle.py`) |
| `source=management_views` trong real_normalization | Snapshot chưa upload — chạy lại bước 4 |
| `DIRECTION_MISSING_IN_REAL` trong compatibility_report | Cross thiếu hướng — bổ sung GPS coordinate hoặc `from_cross_direction` đúng. Xem [troubleshooting.md §3.9](troubleshooting.md) |
| `direction_map` rỗng cho 1 cross sau compile | Cả GPS lẫn legacy code đều không xác định được — recompile sau khi sửa payload |
| `401 Unauthorized` | Header `X-Internal-API-Key: sondinh2k3` |
| `404 Not Found` | Sai port — `/internal/sync/*` và `/ops/*` dùng `8002`; `/api/algorithm/ai` dùng `8001` |
| `bundle-tooling not found` khi build | Sibling repo `bundle-tooling/` chưa đặt ngang hàng |
| Log `[auto-sync] CANH BAO: ... overlap ...` | `SIM_BUNDLE_PREFIX` và `ARTIFACT_BUNDLE_PREFIX` overlap — sửa `docker-compose.yml` |

Reset hoàn toàn:

```bash
docker compose --profile db --profile storage --profile app down -v
docker compose --profile db --profile storage --profile app up -d --build
```

Chi tiết debug: [troubleshooting.md](troubleshooting.md).

---

## 11. Workflow rút gọn cho lần test thứ 2 trở đi

Sau khi đã chạy thành công lần đầu, các lần sau chỉ cần:

```bash
# 1. Start stack (nếu chưa chạy)
docker compose --profile db --profile storage --profile app up -d

# 2. (Nếu snapshot/bundle còn) chạy inference luôn:
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -d @test_cologne3_payload.json
```

Pipeline persistent qua Docker volumes: snapshot + active bundle giữ nguyên giữa các lần restart, miễn là không `down -v`.

---

## 12. Áp dụng cho mạng lưới khác (production)

Khi đổi sang network thật:

| Bước | Thay đổi |
|---|---|
| 4. Real Network Snapshot | Backend export từ DB quản lý theo schema ở [PIPELINE.md §4.2](PIPELINE.md). `tenantId/networkId` mới. |
| 5. Sim Bundle | Training team build với `--tenant-id` + `--network-id` mới. Path `sim/{tenant}/{network}/...` |
| 6. Active bundle | Service tự compose; không cần can thiệp |
| 7. Inference | Core Controller gửi `areaId` mới |

Checklist chuyển production:

- [ ] Backend gửi đầy đủ `area + areaCrosses + crosses + roads + cycles + stages + simToReal`.
- [ ] Đảm bảo `simToReal` mapping explicit (không rely vào `AUTO_CROSS_MAPPING_BY_ORDER`).
- [ ] Cấu hình `INTERNAL_API_KEY` mạnh per-customer.
- [ ] Đặt `AI_STRICT_MODE=true` ở production.
- [ ] `SIM_BUNDLE_AUTO_ACTIVATE=false` ở production (manual review compatibility report trước khi activate).

Tài liệu deploy production: [deployment.md](deployment.md). Production rollout staging→shadow→pilot: [integration-real-controller.md](integration-real-controller.md).

---

## 13. Tham khảo nhanh

| Tình huống | Đọc |
|---|---|
| Muốn hiểu pipeline overview trước | [PIPELINE.md](PIPELINE.md) |
| Muốn xem chi tiết endpoint | [api-reference.md](api-reference.md) |
| Muốn deploy customer mới | [deployment.md](deployment.md) |
| Muốn tích hợp Core Controller thật | [integration-real-controller.md](integration-real-controller.md) |
| Lỗi không xử lý được trong §10 | [troubleshooting.md](troubleshooting.md) |
| Muốn hiểu sim_to_real mapping | [sim-to-real-mapping.md](sim-to-real-mapping.md) |
| Env vars chi tiết | [configuration.md](configuration.md) |
