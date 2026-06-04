# Core Controller Integration API

Tài liệu này là contract tích hợp giữa phần mềm điều khiển đèn và AI Algorithm Service. Phía Core Controller chỉ cần dùng hai API chính:

1. Đăng ký/cập nhật snapshot mạng lưới thực.
2. Gửi dữ liệu runtime để AI Service inference và nhận plan đèn đề xuất.

AI Service không trực tiếp điều khiển TSC. Core Controller vẫn là nơi validate output, quyết định actuate, và fallback fixed-time khi AI Service lỗi hoặc quá timeout.

Nếu cần tài liệu riêng về cách tổ chức dữ liệu đầu vào, đọc [core-controller-input-data.md](core-controller-input-data.md).

## 1. Service và base URL

| Luồng | Service | Port local | Caller | Mục đích |
|---|---|---:|---|---|
| Sync topology | `ai-ops` | `8002` | Backend quản trị/Core management | Đăng ký snapshot mạng thực, compile static metadata |
| Runtime inference | `ai-runtime` | `8001` | Core Controller | Gửi state động, nhận plan tối ưu |

Production có thể deploy sau gateway/reverse proxy. Khi đó thay `http://localhost:8001` và `http://localhost:8002` bằng domain nội bộ tương ứng.

## 2. Luồng tích hợp chuẩn

1. Khi topology/cycle/stage/road thay đổi, backend quản trị gọi:

```http
PUT /internal/sync/areas/{area_id}/real-network
```

2. AI Service lưu snapshot vào DB nội bộ và tự compile:

```text
models/real_normalization/area_<area_id>/
```

3. Model bundle mô phỏng đã được upload/activate theo `tenantId + networkId`. Phần này là lifecycle của AI Service, Core Controller không cần gọi trong chu kỳ runtime.

4. Mỗi chu kỳ điều khiển, Core Controller gọi:

```http
POST /api/algorithm/ai
```

5. Core Controller validate response. Nếu hợp lệ thì push plan xuống TSC. Nếu lỗi, timeout, hoặc output không hợp lệ thì dùng fixed-time fallback.

## 3. Định danh bắt buộc

| Field | Nguồn sinh | Dùng ở đâu | Ý nghĩa |
|---|---|---|---|
| `area_id` / `areaId` | DB quản lý mạng thực | URL sync, body inference | Vùng/nút mạng thực mà Core Controller đang điều khiển |
| `tenantId` | Cấu hình tích hợp | Snapshot, model bundle manifest | Phân tách khách hàng/môi trường |
| `networkId` | Cấu hình tích hợp | Snapshot, model bundle manifest | Tên logic để ghép model mô phỏng với mạng thực |
| `simToReal` | UI/operator overlay | Snapshot | Mapping ID cross trong mô phỏng sang ID cross thực |

Quan trọng: `areaId` không cần giống ID trong mô phỏng. AI Service ghép model bundle với snapshot thực bằng `tenantId + networkId`, sau đó dùng `simToReal` để biết cross mô phỏng nào tương ứng cross thực nào.

Ví dụ production:

```json
{
  "areaId": 1308700,
  "tenantId": "default",
  "networkId": "cologne3",
  "simToReal": {
    "33202549": 33000000101001,
    "360082": 33000000101002
  }
}
```

## 4. API đăng ký snapshot mạng thực

### 4.1 Endpoint

```http
PUT /internal/sync/areas/{area_id}/real-network
```

Base URL local:

```text
http://localhost:8002
```

### 4.2 Headers

| Header | Bắt buộc | Mô tả |
|---|---:|---|
| `Content-Type: application/json` | Có | Payload JSON |
| `X-Internal-API-Key` | Có | API key nội bộ, khớp biến môi trường `INTERNAL_API_KEY` |
| `X-Request-Id` | Khuyến nghị | Trace ID để đối chiếu log/audit |

### 4.3 Idempotency

Mỗi request phải có `sourceEventId`. Đây là idempotency key.

| Tình huống | Kết quả |
|---|---|
| Cùng `sourceEventId`, cùng payload | Trả `status=duplicate`, không ghi lại |
| Cùng `sourceEventId`, payload khác | Trả lỗi `SYNC_IDEMPOTENCY_CONFLICT` |
| Snapshot mới/cập nhật topology | Dùng `sourceEventId` mới |

Không dùng literal như `evt-real-network-REPLACE-ME-<timestamp>` trong production. Hãy sinh ID thật, ví dụ `real-network-1308700-20260603T093000-v17`.

### 4.4 Request body

Schema tổng:

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---:|---|
| `sourceEventId` | string | Có | Idempotency key của lần sync |
| `tenantId` | string | Khuyến nghị | Tenant logic, mặc định `default` nếu không gửi |
| `networkId` | string | Khuyến nghị | Network logic để ghép với model bundle, mặc định `area_<area_id>` nếu không gửi |
| `schemaVersion` | string | Có | Hiện tại dùng `real-network/v1` |
| `sourceVersion` | string | Khuyến nghị | Version export từ DB quản lý |
| `area` | object | Có | Thông tin area |
| `areaCrosses` | array | Có | Mapping area -> cross -> cycle |
| `crosses` | array | Có | Danh sách nút giao |
| `roads` | array | Có | Danh sách nhánh đường/approach |
| `cycles` | array | Có | Cycle tĩnh của từng cross |
| `stages` | array | Có | Stage/phase tĩnh của từng cycle |
| `simToReal` | object | Có | Overlay mapping sim cross ID -> real cross ID |

### 4.5 Các nhóm dữ liệu trong snapshot

`area`:

| Field | Mô tả |
|---|---|
| `area_id` | Phải khớp `{area_id}` trên URL |
| `area_name` | Tên hiển thị |
| `is_active` | `1`/`true` nếu area đang dùng |

`areaCrosses`:

| Field | Mô tả |
|---|---|
| `area_id` | ID area thực |
| `cross_id` | ID cross thực |
| `cycle_id` | Cycle đang gắn với cross |
| `is_active` | `1`/`true` nếu mapping đang dùng |

`crosses`:

| Field | Mô tả |
|---|---|
| `id` | ID cross thực |
| `location` | Khuyến nghị dạng `"lat,lon"` để service tự suy direction ổn định |
| `old_id` | ID cũ/ID nguồn nếu có, dùng để trace |
| `is_active` | `1`/`true` nếu cross đang dùng |

`roads`:

| Field | Mô tả |
|---|---|
| `id` | ID road thực |
| `from_cross` | Cross mà road đi vào/thuộc về |
| `from_cross_direction` | Direction từ DB nếu có |
| `to_cross` | Cross kế tiếp nếu road nối sang nút khác |
| `to_cross_direction` | Direction tại cross kế tiếp nếu có |
| `number_of_lanes` | Số làn |
| `length` | Chiều dài road, đơn vị mét nếu có |
| `capacity_design` | Saturation/design flow, đơn vị xe/giờ |
| `speed_design` | Tốc độ thiết kế |
| `coordinates` | Khuyến nghị polyline GPS để suy direction và topology |

`cycles`:

| Field | Mô tả |
|---|---|
| `id` | ID cycle thực |
| `cross_id` | Cross sở hữu cycle |
| `cycle_length` | Tổng chu kỳ, tính cả green + yellow + red_clear |
| `yellow` | Thời gian vàng mặc định |
| `red_clear` | Thời gian all-red/red-clear mặc định |
| `number_of_stages` | Số stage trong cycle |
| `cycle_type` | Loại cycle nếu hệ thống có |
| `old_id` | ID nguồn nếu có |

`stages`:

| Field | Mô tả |
|---|---|
| `id` | ID stage thực |
| `cycle_id` | Cycle sở hữu stage |
| `order_number` | Thứ tự stage |
| `green` | Green fixed-time hiện tại/mặc định |
| `yellow` | Yellow riêng của stage nếu có |
| `red_clear` | Red-clear/all-red riêng của stage nếu có |
| `min_green_time` | Green tối thiểu theo cấu hình TSC |
| `max_green_time` | Green tối đa theo cấu hình TSC |
| `old_id` | Stage ID trong nguồn/mô phỏng nếu có |

`simToReal`:

```json
{
  "33202549": 33000000101001,
  "360082": 33000000101002
}
```

Key là cross ID phía mô phỏng/training. Value là cross ID thực trong snapshot. Toàn bộ value phải tồn tại trong `crosses[].id`.

### 4.6 Request ví dụ

File ví dụ đầy đủ:

```text
docs/api-payload-examples/register-real-network-snapshot.json
```

Curl:

```bash
curl -X PUT "http://localhost:8002/internal/sync/areas/1308700/real-network" \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  -H "X-Request-Id: sync-1308700-20260603-001" \
  -d @docs/api-payload-examples/register-real-network-snapshot.json
```

### 4.7 Success response

```json
{
  "status": "applied",
  "areaId": 1308700,
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "checksum": "sha256...",
  "counts": {
    "areaCrosses": 5,
    "crosses": 5,
    "roads": 24,
    "cycles": 5,
    "stages": 15
  },
  "realNormalization": {
    "status": "ok",
    "outputDir": "/app/models/real_normalization/area_1308700"
  },
  "retryPendingSimBundles": {
    "retried": 0
  }
}
```

Sau response này, runtime có thể hydrate static metadata cho inference compact từ snapshot đã compile.

### 4.8 Verify snapshot đã compile

Endpoint này là tùy chọn, dùng để debug/verify:

```http
GET /internal/sync/areas/{area_id}/real-normalization
```

Curl:

```bash
curl "http://localhost:8002/internal/sync/areas/1308700/real-normalization" \
  -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  -H "X-Request-Id: verify-real-normalization-1308700"
```

Nếu `direction_map` của một cross rỗng hoặc thiếu stage/cycle metadata, cần kiểm tra lại snapshot trước khi go-live.

## 5. API inference

### 5.1 Endpoint

```http
POST /api/algorithm/ai
```

Base URL local:

```text
http://localhost:8001
```

### 5.2 Headers

| Header | Bắt buộc | Mô tả |
|---|---:|---|
| `Content-Type: application/json` | Có | Payload JSON |
| `X-Request-Id` | Rất khuyến nghị | Trace ID để audit input/output và latency |

Endpoint runtime không yêu cầu `X-Internal-API-Key` trong cấu hình hiện tại.

### 5.3 Timeout và retry

Khuyến nghị phía Core Controller:

| Tham số | Giá trị |
|---|---:|
| Timeout HTTP | `500 ms` |
| Retry | Tối đa `1` lần |
| Retry khi | Timeout hoặc network error |
| Không retry khi | Response HTTP 4xx do input/config |
| Fallback | Fixed-time plan đã cấu hình sẵn |

Không bao giờ để TSC mất plan. Nếu AI Service không trả được output hợp lệ trong SLA, Core Controller phải dùng fallback.

### 5.4 Request body compact

Production inference không gửi lại topology tĩnh. Snapshot đã đăng ký trước sẽ cung cấp `cycleLength`, `yellow`, `redClear`, `direction`, `saturationFlow`, mapping phase và network topology.

Schema tổng:

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---:|---|
| `areaId` | number | Có | Area thực cần inference |
| `timestamp` | string | Khuyến nghị | Thời điểm đo dữ liệu |
| `crosses` | array | Có | Danh sách cross cần tính |

Các tham số điều chỉnh runtime như `RUNTIME_MIN_GREEN`, `RUNTIME_MAX_GREEN`,
`RUNTIME_GREEN_TIME_STEP` thuộc cấu hình AI Service, không nằm trong request
body của Core Controller.

Mỗi `crosses[]`:

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---:|---|
| `crossId` | number | Có | ID cross thực |
| `cycleId` | number | Khuyến nghị | Cycle hiện tại tại cross |
| `stages` | array | Có | Stage hiện tại |
| `roads` | array | Có | Traffic demand trên các road vào cross |

Mỗi `stages[]`:

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---:|---|
| `stageId` | number | Có | ID stage thực |
| `greenTime` | number | Có | Green hiện tại hoặc fixed-time hiện tại |

Mỗi `roads[]`:

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---:|---|
| `roadId` | number | Có | ID road thực |
| `averageSpeed` | number | Có | Tốc độ trung bình, mặc định đơn vị `km/h` |
| `averageSpeedUnit` | string | Tùy chọn legacy | Chỉ gửi nếu cần override sang `"m/s"` |
| `occupancySpace` | number | Có | Mức chiếm dụng, `0..100` |
| `queueLength` | number | Khuyến nghị | Hàng đợi, mét hoặc ratio `0..1` nếu chuẩn hóa |
| `totalVehicle` | number | Khuyến nghị | Số xe trong cửa sổ đo |
| `windowSeconds` | number | Khuyến nghị | Độ dài cửa sổ đo |
| `density` | number | Tùy chọn | Mật độ nếu sensor có sẵn |

Không nên gửi các field sau trong runtime nếu snapshot đã đúng: `cycleLength`, `yellow`, `redClear`, `direction`, `saturationFlow`, `toCrossId`, road coordinates. Các field này vẫn được service nhận cho legacy/override, nhưng production nên để AI Service hydrate từ snapshot để tránh mismatch.

### 5.5 Request ví dụ

File ví dụ compact:

```text
docs/api-payload-examples/inference-compact-request.json
```

Ví dụ rút gọn:

```json
{
  "areaId": 1308700,
  "timestamp": "2026-05-24T10:00:00+07:00",
  "crosses": [
    {
      "crossId": 33000000101001,
      "cycleId": 1001,
      "stages": [
        {
          "stageId": 89101,
          "greenTime": 41
        },
        {
          "stageId": 89102,
          "greenTime": 41
        }
      ],
      "roads": [
        {
          "roadId": 700001,
          "averageSpeed": 28.0,
          "occupancySpace": 40.0,
          "totalVehicle": 12,
          "queueLength": 15,
          "windowSeconds": 60
        }
      ]
    }
  ]
}
```

Curl:

```bash
curl -X POST "http://localhost:8001/api/algorithm/ai" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: infer-1308700-20260603-001" \
  -d @docs/api-payload-examples/inference-compact-request.json
```

### 5.6 Success response

```json
{
  "status": 1,
  "numIntersections": 1,
  "areaIds": [1308700],
  "algorithmOutputs": [
    {
      "crossId": 33000000101001,
      "areaId": 1308700,
      "crossName": "Cross 33000000101001",
      "cycleId": 1001,
      "cycleLength": 90,
      "createdDate": "2026-06-03T09:30:00+07:00",
      "phases": [
        {
          "stageId": 89101,
          "stageCode": "S1",
          "oldId": "0",
          "greenTime": 42,
          "yellowTime": 3,
          "redClearTime": 1
        },
        {
          "stageId": 89102,
          "stageCode": "S2",
          "oldId": "2",
          "greenTime": 40,
          "yellowTime": 3,
          "redClearTime": 1
        }
      ]
    }
  ]
}
```

Ý nghĩa:

| Field | Mô tả |
|---|---|
| `status` | `1` là inference thành công |
| `numIntersections` | Số cross được tính |
| `areaIds` | Area thực được route trong request |
| `algorithmOutputs[]` | Plan đề xuất cho từng cross |
| `cycleLength` | Tổng chu kỳ output |
| `phases[]` | Danh sách stage với green/yellow/red-clear mới |

### 5.7 Validate output trước khi actuate

Core Controller phải validate tối thiểu:

| Check | Điều kiện pass |
|---|---|
| HTTP status | `200` |
| JSON parse | Thành công |
| Business status | `status == 1` |
| Area | `areaIds` chứa đúng `areaId` đang gọi |
| Cross coverage | Mỗi cross cần điều khiển có đúng một output |
| Cycle/stage | `cycleId`, `stageId` thuộc topology hiện tại |
| Tổng chu kỳ | `sum(greenTime + yellowTime + redClearTime)` xấp xỉ `cycleLength`, tolerance nên `<= 1s` |
| Green range | `greenTime` nằm trong min/max cho phép của TSC |

Nếu bất kỳ check nào fail, không push plan AI xuống TSC. Hãy fallback fixed-time và ghi audit log.

## 6. Error response

Format lỗi chung:

```json
{
  "errorCode": "INVALID_INPUT",
  "message": "Cross 33000000101005 cycle=1005 thiếu cycleLength.",
  "path": "/api/algorithm/ai",
  "requestId": "b5e38c4d-6330-4c51-a897-8b2a7d812da1",
  "areaId": 1308700
}
```

Các mã lỗi thường gặp:

| `errorCode` | HTTP | Ý nghĩa | Cách xử lý phía Core Controller |
|---|---:|---|---|
| `INVALID_INPUT` | 400 | Payload thiếu/sai field | Không retry, fallback, sửa mapping dữ liệu |
| `MULTIPLE_AREAS_NOT_ALLOWED` | 400 | Một request chứa nhiều area khi service chỉ cho phép một area | Tách request theo area |
| `AREA_NOT_FOUND` | 404 | Area chưa được đăng ký | Sync snapshot trước |
| `CONFIG_NOT_FOUND` | 404 | Thiếu real normalization/config | Sync hoặc recompile snapshot |
| `POLICY_NOT_FOUND` | 404 | Chưa có policy/runtime bundle | Kiểm tra lifecycle model bundle |
| `AREA_NOT_READY` | 409 | Area chưa đủ điều kiện inference | Fallback, chờ ops xử lý |
| `SYNC_IDEMPOTENCY_CONFLICT` | 409 | `sourceEventId` đã dùng với payload khác | Sinh `sourceEventId` mới cho snapshot mới |
| `UNAUTHORIZED` | 401 | Thiếu/sai `X-Internal-API-Key` ở ops API | Kiểm tra cấu hình key |
| `INTERNAL_ERROR` | 500 | Lỗi service | Retry tối đa 1 lần nếu runtime, sau đó fallback |

## 7. Audit log phía Core Controller

Mỗi lần gọi inference nên lưu:

| Nhóm | Nội dung |
|---|---|
| Request trace | `X-Request-Id`, timestamp gửi, latency |
| Input | Payload gửi vào AI Service hoặc hash payload |
| Output | Response nhận được hoặc error body |
| Actuation decision | `AI_PLAN_APPLIED` hoặc `FALLBACK_FIXED_TIME` |
| Fallback reason | Timeout, HTTP error, invalid output, stale sensor |

Quy tắc thực tế: cùng một `X-Request-Id` nên xuất hiện trong log Core Controller và log AI Service để điều tra sự cố nhanh.

## 8. Checklist tích hợp

Trước khi chạy production:

| Hạng mục | Pass khi |
|---|---|
| Snapshot | `PUT /real-network` trả `status=applied` |
| Static compile | `realNormalization.status == "ok"` |
| Mapping | `simToReal` đủ toàn bộ cross model cần chạy |
| Model runtime | Area readiness pass hoặc inference test trả `status=1` |
| Payload runtime | Chỉ gửi state động và traffic demand |
| Timeout | HTTP client đặt timeout 500 ms |
| Fallback | Fixed-time fallback luôn sẵn sàng |
| Audit | Có `X-Request-Id` và lưu input/output |

## 9. Tóm tắt trách nhiệm

| Thành phần | Trách nhiệm |
|---|---|
| Backend quản trị/Core management | Export snapshot tĩnh, cấu hình `tenantId`, `networkId`, `simToReal`, gọi sync API khi topology đổi |
| AI Service `ai-ops` | Lưu snapshot, compile real normalization, compose/activate runtime bundle |
| AI Service `ai-runtime` | Hydrate static metadata, chạy model, trả plan đề xuất |
| Core Controller | Gửi state động, validate output, actuate TSC hoặc fallback |
