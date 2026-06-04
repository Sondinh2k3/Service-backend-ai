# API Contract: Core Controller <-> AI Service

Tài liệu này dành cho đội tích hợp Core Controller. Mục tiêu là implement runtime call an toàn và sync topology đúng cho production.

Nếu chỉ cần contract tích hợp hai API chính, đọc bản tập trung tại [core-controller-integration-api.md](core-controller-integration-api.md). File hiện tại giữ vai trò tài liệu mở rộng cho các endpoint runtime/ops khác.

## 1. AI Service endpoints

Hai endpoint dưới đây đều thuộc **AI Algorithm Service backend**. Core Controller không chạy `ai-runtime`; Core Controller chỉ gọi API runtime do AI Service expose ra.

| AI Service component | Port | Nhiệm vụ | External caller |
|---|---:|---|---|
| `ai-runtime` | 8001 | Inference, readiness, cache | Core Controller |
| `ai-ops` | 8002 | Sync topology, bundle lifecycle, auto-sync | Backend quản trị/DevOps |

Production nên tách AI Service thành 2 container/process:

| Container | `SERVICE_ROLE` | Ghi chú |
|---|---|---|
| `ai-runtime` | `runtime` | Public API cho Core Controller gọi inference |
| `ai-ops` | `ops` | Internal API cho sync topology, bundle lifecycle, auto-sync |

Nếu triển khai đơn giản hơn, có thể chạy chung một process với `SERVICE_ROLE=all`, nhưng production nên tách để cô lập runtime inference khỏi tác vụ ops.

## 2. Headers

| Header | Required | Used by | Note |
|---|---:|---|---|
| `Content-Type: application/json` | yes | all JSON APIs |  |
| `X-Request-Id` | strongly recommended | runtime + ops | Dùng để audit, trace, điều tra sự cố |
| `X-Internal-API-Key` | yes for internal/ops | sync + ops | Giá trị theo `INTERNAL_API_KEY` |

## 3. Runtime flow

Core Controller lặp lại mỗi chu kỳ điều khiển bằng cách gọi API của `ai-runtime`:

1. Thu thập trạng thái TSC/sensor.
2. Map state nội bộ sang `AIInput` gọn: trạng thái đèn hiện tại + nhu cầu giao thông.
3. Gọi `POST /api/algorithm/ai` với timeout 500 ms.
4. Retry tối đa 1 lần nếu timeout/network error.
5. Validate output.
6. Nếu valid: push plan xuống TSC.
7. Nếu invalid/timeout/error: dùng fixed-time fallback.
8. Audit input, output, latency, `X-Request-Id`, fallback reason.

AI Service không actuate đèn. Quyền actuate nằm hoàn toàn ở Core Controller.

### 3.1 Runtime payload production

Topology tĩnh đã được sync trước qua `ai-ops`, vì vậy Core Controller **không cần gửi lại** toàn bộ network mỗi lần inference.

Core Controller chỉ nên gửi:

| Nhóm | Field |
|---|---|
| Area/cross | `areaId`, `crossId` |
| Cycle hiện tại | `cycleId`; `cycleLength` chỉ gửi khi muốn override/legacy |
| Stage hiện tại | `stageId`, `greenTime` hoặc `duration` |
| Traffic demand | `roadId`, `averageSpeed` mặc định `km/h`, `occupancySpace`, `queueLength`, `totalVehicle`, `windowSeconds`, `density` nếu có |

Các field như `cycleLength`, `direction`, `toCrossId`, `saturationFlow`, `stageCode`, `oldId`, `yellow`, `redClear`, road coordinates được hydrate từ real normalization đã compile từ snapshot. Active runtime bundle cung cấp policy/model và có thể bổ sung phase mapping phục vụ model.

## 4. Runtime APIs

### `GET /health`

Dùng cho liveness.

```bash
curl http://localhost:8001/health
```

### `GET /ready`

Dùng cho readiness của service. Nếu false, Core Controller vẫn phải fallback.

```bash
curl http://localhost:8001/ready
```

### `GET /api/algorithm/ai/areas`

Trả danh sách area visible/ready.

### `GET /api/algorithm/ai/areas/{area_id}/readiness`

Trả readiness chi tiết của area: policy, meta, network, bundle version.

### `POST /api/algorithm/ai`

Endpoint inference chính. Schema chi tiết xem [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

Core Controller action:

| Result | Action |
|---|---|
| `200` + `status == 1` + plan valid | Push plan |
| Timeout | Retry 1 lần, sau đó fallback |
| `4xx/5xx` | Fallback, log |
| Response parse fail | Fallback, log raw body |
| Cycle duration invalid | Fallback, log validation error |

### `POST /api/algorithm/ai/cache/clear`

Debug/admin endpoint để clear runtime cache. Production chỉ nên dùng khi có quy trình ops.

## 5. Sync flow

Backend quản trị/Core management system gọi API của `ai-ops` khi topology/config thay đổi. Đây vẫn là API thuộc AI Service backend, không phải phần mềm điều khiển đèn trực tiếp.

### 5.1 Sync area metadata

```http
PUT /internal/sync/areas/{area_id}
X-Internal-API-Key: <key>
```

```json
{
  "sourceEventId": "area-1-v1",
  "tenantId": "tenant_kh1",
  "networkId": "network_hn_001",
  "areaName": "Area 1",
  "isActive": true,
  "controllerVisible": true
}
```

### 5.2 Sync real network snapshot

```http
PUT /internal/sync/areas/{area_id}/real-network
X-Internal-API-Key: <key>
```

Payload gom:

| Field | Source | Production note |
|---|---|---|
| `area` | DB management | Real area |
| `areaCrosses` | DB management | Crosses thuộc area |
| `crosses` | DB management | Nên có `location: "lat,lon"` |
| `roads` | DB management | Nên có `coordinates` từ `v_road_coordinate` |
| `cycles` | DB management | Cycle thật |
| `stages` | DB management | Stage thật |
| `simToReal` | mapping overlay | Không có sẵn trong DB; phải confirm riêng |

Snapshot nên bao gồm các static field phục vụ runtime hydrate:

- `cycles[].cycle_length`, `cycle_name`, `created_date`.
- `stages[].stage_code`, `old_id`, `green`, `yellow`, `red_clear`, `min_green_time`, `max_green_time`.
- `roads[].number_of_lanes`, `length`, `speed_design`, `capacity_design`.

`simToReal` là mapping:

```json
{
  "simToReal": {
    "33202549": 567001,
    "360082": 567002
  }
}
```

Production không được phụ thuộc vào auto-map theo thứ tự. Nếu report có warning `AUTO_CROSS_MAPPING_BY_ORDER`, dừng activate.

### 5.3 Verify real normalization

```http
GET /internal/sync/areas/{area_id}/real-normalization
X-Internal-API-Key: <key>
```

Check:

- Có `crosses`.
- Mỗi cross có `direction_map`.
- `cycles` có `cycle_length` và stage static nếu runtime muốn dùng payload gọn.
- `sim_to_real` đã có mapping explicit/confirmed.

### 5.4 Recompile normalization

```http
POST /internal/sync/areas/{area_id}/real-normalization/recompile
X-Internal-API-Key: <key>
```

Dùng khi snapshot đã sửa và cần compile lại.

## 6. Ops APIs usually needed

| Endpoint | Purpose |
|---|---|
| `GET /ops/auto-sync/status` | Kiểm tra listener/poller |
| `POST /ops/auto-sync/scan-now` | Scan MinIO thủ công |
| `POST /ops/sim-bundles/pull` | Pull sim bundle và compose runtime bundle |
| `GET /ops/bundles` | List bundle |
| `GET /ops/networks/{network_id}/active` | Xem active bundle |
| `POST /ops/bundles/{bundle_id}/activate` | Activate bundle sau review |
| `POST /ops/networks/{network_id}/rollback` | Rollback về bundle trước |

Tất cả endpoint ops cần `X-Internal-API-Key`.

## 7. Production checklist

Before runtime:

- `ai-runtime /ready` true.
- Area readiness true.
- Active bundle đúng `tenantId/networkId`.
- Core Controller có fixed-time fallback.
- Timeout/retry/audit đã implement.

Before go-live bundle:

- Real snapshot có topology đầy đủ.
- `simToReal` đã confirm.
- Real normalization có `direction_map`, `cycle_length`, stage `yellow/red_clear`, road static.
- `compatibility_report.json` không có error.
- Không có warning `AUTO_CROSS_MAPPING_BY_ORDER`.
- Manual activate nếu production đặt `SIM_BUNDLE_AUTO_ACTIVATE=false`.

During operation:

- Mỗi request có `X-Request-Id`.
- Log input/output AI để audit.
- Nếu AI lỗi, TSC vẫn có plan fixed-time.
- Alert khi latency, fallback rate, drift, guardrail violations tăng.

## 8. Common mistakes

| Mistake | Correct behavior |
|---|---|
| Coi AI output là lệnh actuate | AI chỉ đề xuất, Core Controller quyết định |
| Export `simToReal` từ DB management | DB không có field này; phải configure/confirm riêng |
| Gửi lại toàn bộ topology ở mỗi inference | Chỉ sync topology khi thay đổi; runtime gửi state động |
| Activate khi có `AUTO_CROSS_MAPPING_BY_ORDER` | Production phải dừng và bổ sung mapping |
| Validate `sum(phase.duration)` | Response dùng `greenTime + yellowTime + redClearTime` |
| Gửi nhiều area trong 1 request | Tách theo area nếu strict mode |

## 9. References

- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md)
- [api-reference.md](api-reference.md)
- [sim-to-real-mapping.md](sim-to-real-mapping.md)
- [integration-real-controller.md](integration-real-controller.md)
