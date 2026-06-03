# Inference API: `POST /api/algorithm/ai`

Endpoint này là contract runtime chính giữa Core Controller và AI Service.

Production flow đúng là:

1. Backend/Core management sync topology tĩnh qua `PUT /internal/sync/areas/{area_id}/real-network`.
2. AI Service compile real normalization và compose/activate runtime bundle.
3. Core Controller gọi inference, chỉ gửi trạng thái đèn hiện tại và nhu cầu giao thông.

Payload legacy gửi đầy đủ topology vẫn được hỗ trợ để tương thích, nhưng payload gọn dưới đây là chuẩn khuyến nghị cho production.

## Endpoint

```http
POST /api/algorithm/ai
Content-Type: application/json
X-Request-Id: <uuid-or-trace-id>
```

Runtime container: `http://<ai-runtime-host>:8001`.

## Client Policy

| Item | Khuyến nghị production |
|---|---|
| Timeout | 500 ms |
| Retry | Tối đa 1 lần với cùng `X-Request-Id` hoặc request id có suffix retry |
| Fallback | Fixed-time plan đã cấu hình sẵn |
| Audit | Lưu request + response + latency + fallback reason |
| Actuation | Chỉ push xuống TSC sau khi validate output |

## Production Request Schema

```json
{
  "areaId": 1,
  "timestamp": "2026-06-02T10:00:00+07:00",
  "crosses": [
    {
      "crossId": 1001,
      "cycleId": 10,
      "stages": [
        {"stageId": 1, "greenTime": 41},
        {"stageId": 2, "greenTime": 42}
      ],
      "roads": [
        {
          "roadId": 501,
          "averageSpeed": 32.5,
          "averageSpeedUnit": "km/h",
          "occupancySpace": 45.2,
          "queueLength": 28,
          "totalVehicle": 120,
          "windowSeconds": 300
        }
      ]
    }
  ]
}
```

### Top-Level

| Field | Type | Required | Note |
|---|---|---:|---|
| `areaId` | integer | recommended | Area đã sync và có active bundle. Có thể đặt trong từng cross nếu cần legacy/multi-area |
| `timestamp` | string/null | no | Timestamp quan sát, dùng cho audit/freshness phía caller |
| `crosses` | array | yes | Danh sách nút trong cùng area. Production nên enforce 1 area/request |
| `yellowTime` | integer | no | Legacy/request override, default `3`; production nên hydrate theo từng stage từ snapshot |
| `minGreen` | integer | no | Default `15` |
| `maxGreen` | integer | no | Default `60` |
| `greenTimeStep` | integer | no | Default `5` |

### Cross

| Field | Type | Required | Note |
|---|---|---:|---|
| `crossId` hoặc `id` | integer | yes | Real cross ID đã có trong snapshot |
| `areaId` | integer | no | Chỉ cần nếu không có top-level `areaId` |
| `cycleId` | integer | no | Nếu bỏ, service dùng `primary_cycle_id` trong bundle |
| `cycleLength` | integer | no | Chỉ gửi khi muốn override/legacy; production hydrate từ real normalization nếu snapshot có `cycle_length` |
| `stages` | array | yes | Trạng thái stage hiện tại |
| `roads` | array | yes | Nhu cầu giao thông theo road |

Không cần gửi `x`, `y`, `direction`, `toCrossId`, `cycleLength`, stage metadata, road static nếu đã sync topology đầy đủ.

### Stage

| Field | Type | Required | Note |
|---|---|---:|---|
| `stageId` hoặc `id` | integer | yes | Real stage ID thuộc cycle/cross đã sync |
| `greenTime` | integer | recommended | Green hiện tại. Service cộng `yellow + redClear` từ snapshot để ra duration nội bộ |
| `duration` | integer | optional | Stage duration hiện tại = `green + yellow + redClear`. Nếu có thì service dùng trực tiếp |
| `yellow` | integer | no | Legacy/override. Production hydrate từ snapshot |
| `redClear` | integer | no | Legacy/override. Production hydrate từ snapshot |
| `stageCode`, `oldId` | string | no | Hydrate từ snapshot nếu có |

Nếu một nút có all-red thì snapshot/stage hoặc request phải có `redClear > 0`. Nếu không có all-red, `redClear = 0`.

### Road

| Field | Type | Required | Note |
|---|---|---:|---|
| `roadId` hoặc `id` | integer | yes | Real road ID đã có trong snapshot |
| `averageSpeed` | number | yes | Tốc độ trung bình |
| `averageSpeedUnit` | string | strongly recommended | `m/s` hoặc `km/h`. Nếu bỏ, service mặc định `m/s` |
| `occupancySpace` | number | yes | Occupancy 0-100 hoặc 0-1 |
| `queueLength` | number/null | recommended | Mét hoặc normalized 0-1 |
| `totalVehicle` | integer/null | recommended | Số xe trong window |
| `windowSeconds` | number/null | recommended | Độ dài window quan sát |
| `density` | number/null | optional | Nếu có, service dùng trực tiếp; nếu không có có thể derive từ `totalVehicle/windowSeconds/speed` |
| `saturationFlow` | number | no | Hydrate từ real normalization/runtime bundle nếu đã sync |
| `direction`, `toCrossId` | number/null | no | Legacy fallback; production dùng `direction_map` và `network.json` |

Nếu không gửi `queueLength`, `totalVehicle`, `windowSeconds`, service vẫn chạy nhưng các channel `queue/density` sẽ fallback theo occupancy, làm nghèo tín hiệu nhu cầu giao thông.

## Static Hydration Rule

Với compact payload, static metadata được lấy theo thứ tự:

1. Real normalization đã compile từ `PUT /internal/sync/areas/{area_id}/real-network`.
2. Active runtime bundle, đặc biệt policy/model metadata và phase mapping.
3. Legacy area config nếu có.

Vì vậy sau khi sync snapshot đầy đủ, Core Controller không cần gửi `cycleLength`, `yellow`, `redClear`, `direction` hay `saturationFlow` trong mỗi request. Nếu service báo thiếu các field này, nguyên nhân thường là snapshot thiếu static metadata, chưa recompile real normalization, hoặc area đang dùng sai `areaId/networkId`.

## Legacy Payload

Payload cũ vẫn hợp lệ:

- `crosses[].id`, `crosses[].areaId`
- `cycle: {id, createdDate, crossName, cycleLength}`
- `stages[]` đầy đủ `id/stageCode/oldId/yellow/redClear/duration`
- `roads[]` có `id/direction/saturationFlow/averageSpeed/occupancySpace`

Legacy phù hợp cho demo hoặc khi chưa có snapshot/bundle đầy đủ. Production nên dùng payload gọn.

## Response Schema

```json
{
  "status": 1,
  "numIntersections": 1,
  "areaIds": [1],
  "algorithmOutputs": [
    {
      "crossId": 1001,
      "areaId": 1,
      "crossName": "Cross 1001",
      "cycleId": 10,
      "cycleLength": 90,
      "createdDate": "2026-06-02T10:00:00+07:00",
      "phases": [
        {
          "stageId": 1,
          "stageCode": "S1",
          "oldId": "1",
          "greenTime": 41,
          "yellowTime": 3,
          "redClearTime": 1
        }
      ]
    }
  ]
}
```

## Output Validation Before TSC

Core Controller phải validate:

- HTTP status là `200`.
- Body parse được JSON.
- `status == 1`.
- Tất cả `crossId` trong request có output tương ứng.
- Stage/cycle IDs hợp với topology hiện tại.
- Tổng duration mỗi cycle xấp xỉ `cycleLength`:

```text
sum(phase.greenTime + phase.yellowTime + phase.redClearTime) ~= cycleLength
```

Nếu bất kỳ điều kiện nào fail: không push AI plan, dùng fixed-time fallback.

## Error Contract

Application errors:

```json
{
  "errorCode": "AREA_NOT_READY",
  "message": "Area is not ready",
  "path": "/api/algorithm/ai",
  "requestId": "..."
}
```

Common codes:

| Code | Meaning | Core Controller action |
|---|---|---|
| `AREA_NOT_READY` | Chưa có active bundle/config | Fallback, alert ops |
| `CONFIG_NOT_FOUND` | Thiếu network/config | Fallback, sync lại topology |
| `MODEL_NOT_FOUND` | Thiếu policy/model file | Fallback, rollback/redeploy |
| `INVALID_INPUT` | Payload sai hoặc thiếu static không hydrate được | Fallback, log input |
| `MULTIPLE_AREAS_NOT_ALLOWED` | Request gom nhiều area khi strict | Tách request theo area |

## Minimal Production Example

```bash
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: demo-001" \
  -d '{
    "areaId": 1,
    "timestamp": "2026-06-02T10:00:00+07:00",
    "crosses": [
      {
        "crossId": 1001,
        "cycleId": 10,
        "stages": [
          {"stageId": 1, "greenTime": 41},
          {"stageId": 2, "greenTime": 42}
        ],
        "roads": [
          {
            "roadId": 501,
            "averageSpeed": 32.5,
            "averageSpeedUnit": "km/h",
            "occupancySpace": 45.2,
            "queueLength": 28,
            "totalVehicle": 120,
            "windowSeconds": 300
          }
        ]
      }
    ]
  }'
```

## See Also

- [../docs/core-controller-api-contract.md](../docs/core-controller-api-contract.md)
- [../docs/api-reference.md](../docs/api-reference.md)
