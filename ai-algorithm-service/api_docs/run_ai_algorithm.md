# Inference API — `POST /api/algorithm/ai`

> Chi tiết schema và behavior của endpoint inference chính. Endpoint này được gọi từ Core Controller (Lớp 1) mỗi chu kỳ điều khiển đèn.
>
> Reference toàn bộ endpoints khác: [../docs/api-reference.md](../docs/api-reference.md).

## 1. Endpoint

```
POST http://<edge-host>:8001/api/algorithm/ai
Content-Type: application/json
[X-Request-Id: <uuid>]   # optional, dùng cho trace
```

**Behavior:**
- Nhận danh sách `crosses` với trạng thái giao thông hiện tại
- Group theo `areaId`
- Enforce **1 area / request** khi `ENFORCE_SINGLE_AREA_PER_REQUEST=true` (default)
- Readiness guard: mỗi area phải register + có artifact / bundle đầy đủ
- Strict mode (`AI_STRICT_MODE=true`): không auto-gen config — thiếu file → `CONFIG_NOT_FOUND`
- Chạy policy ONNX → Phase Normalizer → Guardrails → trả signal plan mới
- Audit mỗi request vào `inference_audit` table (request_id, area_id, policy_version, bundle_id, latency, guardrail_triggered, status)
- Drift detection observe `obs_mean` của observation đã z-scored

**Service tự chọn bundle nào dùng cho area:** đọc `active.json` của network gắn với `areaId`. Vendor update model qua MinIO → service tự pickup, customer không cần làm gì (xem [../docs/auto-sync.md](../docs/auto-sync.md)).

## 2. Request schema (AIInput)

### 2.1 Top-level fields

| Field | Type | Required | Default | Note |
|-------|------|----------|---------|------|
| `crosses` | `Array<Cross>` | ✓ | — | Tất cả cross phải cùng `areaId` |
| `cycleTime` | `int` | — | `90` | Range 30..300 |
| `yellowTime` | `int` | — | `3` | Range 1..10 |
| `minGreen` | `int` | — | `10` | Range 5..30 |
| `maxGreen` | `int` | — | `60` | Range 30..120 |
| `greenTimeStep` | `int` | — | `5` | Range 1..15 |

### 2.2 Cross object

| Field | Type | Required | Note |
|-------|------|----------|------|
| `id` | `int` | ✓ | Cross ID (≥1) |
| `areaId` | `int` | ✓ | Area ID — service tra cứu bundle qua đây |
| `type` | `int` | ✓ | Cross type code |
| `cycle` | `Cycle` | ✓ | Xem [2.3] |
| `stages` | `Array<Stage>` | ✓ | Số stage hiện tại của cross |
| `roads` | `Array<Road>` | ✓ | Đầy đủ roads của cross |

### 2.3 Cycle object

```json
{
  "id": 1,
  "createdDate": "2026-04-24T12:00:00Z",
  "createdBy": "system",
  "modifiedDate": "2026-04-24T12:00:00Z",
  "modifiedBy": "system",
  "isActive": 1,
  "crossId": 1,
  "numberOfStages": 2,
  "oldId": "C_1",
  "cycleLength": 90
}
```

### 2.4 Stage object

```json
{
  "id": 1,
  "stageCode": "Phase 0",
  "oldId": "p0",
  "primary": 1,
  "weight": 1.0,
  "minGreenTime": 15,
  "maxGreenTime": 120,
  "yellow": 3,
  "redClear": 1,
  "duration": 52,
  "movements": [
    {"fromRoadId": 1, "toRoadId": 2, "proportion": 0.5}
  ]
}
```

### 2.5 Road object

```json
{
  "id": 1,
  "roadName": "N2C",
  "direction": 1,
  "numberOfLanes": 3,
  "flowRoad": 500,
  "saturationFlow": 5400,
  "averageSpeed": 6.94,
  "averageSpeedUnit": "m/s",
  "occupancySpace": 15.0,
  "totalVehicle": 3,
  "windowSeconds": 60,
  "queueLength": 8.0,
  "density": null,
  "insideArea": 1,
  "length": 142.34
}
```

**Direction codes:** runtime payload chấp nhận encoding 4-direction (`1=N, 2=E, 3=S, 4=W`) — đây là format Core Controller hiện tại đang dùng.

> **Note**: nếu controller chuyển sang encoding 8-direction (`0=N, 2=E, 4=S, 6=W`, với `1/3/5/7` là NE/SE/SW/NW), runtime cold-start fallback ở [src/preprocessing/topology_builder.py](../src/preprocessing/topology_builder.py) hiện CHỈ chấp nhận 1..4 — cần đồng bộ thêm. Đường tin cậy hơn là để service tự suy `direction_map` qua GPS ở compile time (xem [docs/PIPELINE.md §4.6](../docs/PIPELINE.md#46-direction-inference-gps-first-legacy-fallback)) — khi đó field `direction` trong payload inference này chỉ đóng vai trò phụ.

### 2.6 Data collection contract (runtime)

**Mục tiêu:** dữ liệu thực tế phải gần với distribution mô phỏng. Runtime sẽ normalize về $[0,1]$.

**Flow + density (khuyến nghị):**
- `flowVehPerSecond = totalVehicle / windowSeconds`
- `averageSpeedUnit` mặc định `m/s` nếu không gửi.
- `densityVehPerKm = (flowVehPerSecond / averageSpeedMps) * 1000`
- Runtime tự normalize density về $[0,1]$ theo lanes + jam density.

**Occupancy + speed:**
- `occupancySpace`: % (0..100)
- `averageSpeed`: theo `averageSpeedUnit` (`m/s` hoặc `km/h`)

Nếu không có `totalVehicle/windowSeconds`, runtime sẽ **không tự suy density** và fallback theo spec trong bundle.

## 3. Response schema (AIOutput)

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
      "crossName": null,
      "cycleId": 1,
      "createdDate": "2026-04-24T12:00:00Z",
      "phases": [
        {
          "stageId": 1,
          "stageCode": "Phase 0",
          "oldId": "p0",
          "greenTime": 47,
          "yellowTime": 3,
          "redClearTime": 1
        },
        {
          "stageId": 2,
          "stageCode": "Phase 2",
          "oldId": "p2",
          "greenTime": 35,
          "yellowTime": 3,
          "redClearTime": 1
        }
      ]
    }
  ]
}
```

**Đặc điểm:**
- `phases[].greenTime` — kết quả AI sau Guardrails. Đảm bảo `min_green ≤ greenTime ≤ max_green` và tổng (`green + yellow + redClear`) ≈ `cycleLength`
- `phases[]` align thứ tự với `crosses[].stages[]` của input
- Stage bị mask (phase_mapping = -1) → giữ nguyên green-time như input

## 4. Errors

Format chuẩn:
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

| Error code | HTTP | Khi nào |
|-----------|------|---------|
| `INVALID_INPUT` | 400 | Body validate fail (Pydantic 422 wrapped → 400 by handler) hoặc `crosses` rỗng |
| `MULTIPLE_AREAS_NOT_ALLOWED` | 400 | Request có >1 area |
| `AREA_NOT_FOUND` | 404 | `areaId` chưa register |
| `AREA_NOT_READY` | 409 | Area register nhưng chưa đủ artifact / bundle / file |
| `CONFIG_NOT_FOUND` | 404 | network.json hoặc cross config thiếu (strict mode) |
| `POLICY_NOT_FOUND` | 404 | Bundle có DB record nhưng policy.onnx mất trên disk |
| `INTERNAL_ERROR` | 500 | Exception khác |

## 5. Latency expectations

| Tình huống | p50 | p95 |
|-----------|-----|-----|
| First request sau khi service start (cold start ONNX) | 100-300ms | 500ms |
| Steady state, 1 cross, model ~1MB | 20-40ms | 60-80ms |
| Steady state, 5 crosses, model ~10MB | 50-100ms | 150-200ms |

Spec yêu cầu < 100ms (p50). Đo qua `ai_inference_latency_ms_bucket` Prometheus metric.

## 6. Headers

### 6.1 X-Request-Id (request)

Optional. Nếu Core Controller gửi, service dùng cho:
- Log line correlation
- `inference_audit.request_id`
- Response header `X-Request-Id`

Nếu không gửi, service tự sinh UUID.

### 6.2 X-Internal-API-Key

**Không yêu cầu** cho `/api/algorithm/ai` (public). Chỉ cần cho `/internal/*` và `/ops/*`.

## 7. Ví dụ đầy đủ

### Request

```http
POST /api/algorithm/ai HTTP/1.1
Content-Type: application/json
X-Request-Id: 60fde875-567a-4ff2-96c8-45d344227600

{
  "crosses": [
    {
      "id": 1,
      "areaId": 1,
      "type": 1,
      "cycle": {
        "id": 1,
        "createdDate": "2026-04-24T12:00:00Z",
        "createdBy": "system",
        "modifiedDate": "2026-04-24T12:00:00Z",
        "modifiedBy": "system",
        "isActive": 1,
        "crossId": 1,
        "numberOfStages": 2,
        "oldId": "C_1",
        "cycleLength": 90
      },
      "stages": [
        {"id": 1, "stageCode": "Phase 0", "oldId": "p0", "primary": 1, "weight": 1.0,
         "minGreenTime": 15, "maxGreenTime": 120, "yellow": 3, "redClear": 1,
         "duration": 52, "movements": []},
        {"id": 2, "stageCode": "Phase 2", "oldId": "p2", "primary": 1, "weight": 1.0,
         "minGreenTime": 15, "maxGreenTime": 120, "yellow": 3, "redClear": 1,
         "duration": 32, "movements": []}
      ],
      "roads": [
        {"id": 1, "roadName": "N2C", "direction": 1, "numberOfLanes": 3,
         "flowRoad": 500, "saturationFlow": 5400, "averageSpeed": 27.78,
         "occupancySpace": 15.0, "insideArea": 1, "length": 142.34},
        {"id": 2, "roadName": "E2C", "direction": 2, "numberOfLanes": 1,
         "flowRoad": 100, "saturationFlow": 1200, "averageSpeed": 13.89,
         "occupancySpace": 10.0, "insideArea": 1, "length": 196.34},
        {"id": 3, "roadName": "S2C", "direction": 3, "numberOfLanes": 3,
         "flowRoad": 500, "saturationFlow": 5400, "averageSpeed": 27.78,
         "occupancySpace": 15.0, "insideArea": 1, "length": 112.11},
        {"id": 4, "roadName": "W2C", "direction": 4, "numberOfLanes": 1,
         "flowRoad": 100, "saturationFlow": 1200, "averageSpeed": 13.89,
         "occupancySpace": 10.0, "insideArea": 1, "length": 200.23}
      ]
    }
  ],
  "cycleTime": 90,
  "yellowTime": 3
}
```

### Response 200

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
      "crossName": null,
      "cycleId": 1,
      "createdDate": "2026-04-24T12:00:00Z",
      "phases": [
        {"stageId": 1, "stageCode": "Phase 0", "oldId": "p0",
         "greenTime": 47, "yellowTime": 3, "redClearTime": 1},
        {"stageId": 2, "stageCode": "Phase 2", "oldId": "p2",
         "greenTime": 35, "yellowTime": 3, "redClearTime": 1}
      ]
    }
  ]
}
```

## 8. Tham khảo

- [../test_payload.json](../test_payload.json) — payload mẫu sẵn dùng
- [../docs/api-reference.md](../docs/api-reference.md) — tất cả endpoints
- [../docs/architecture.md](../docs/architecture.md) — pipeline 4 bước (Topology → ONNX → Phase → Guardrails)
- [../docs/troubleshooting.md](../docs/troubleshooting.md) — common errors khi tích hợp
- [../postman/README.md](../postman/README.md) — Postman collection có sẵn
