# Core Controller Input Data Contract

Tài liệu này mô tả cách tổ chức dữ liệu đầu vào mà phía phần mềm/Core Controller hoặc backend quản trị cần gửi sang AI Algorithm Service.

Có đúng hai loại input production:

1. `RealNetworkSnapshot`: dữ liệu tĩnh của mạng lưới thực, gửi khi đăng ký/cập nhật area.
2. `AIInput`: dữ liệu động tại thời điểm điều khiển, gửi mỗi lần inference.

Mục tiêu là không gửi thừa topology trong runtime, nhưng cũng không thiếu metadata để AI Service hydrate được cycle, stage, road và mapping model.

## 1. Nguyên tắc phân loại dữ liệu

| Loại dữ liệu | Gửi ở API nào | Tần suất | Ví dụ |
|---|---|---|---|
| Topology tĩnh | `PUT /internal/sync/areas/{area_id}/real-network` | Khi tạo area hoặc topology thay đổi | area, cross, road, cycle, stage, GPS, lane, capacity |
| Mapping sim -> real | `PUT /internal/sync/areas/{area_id}/real-network` | Khi cấu hình/đổi model hoặc đổi topology | `simToReal` |
| Trạng thái đèn hiện tại | `POST /api/algorithm/ai` | Mỗi chu kỳ inference | `cycleId`, `stageId`, `greenTime` |
| Nhu cầu giao thông | `POST /api/algorithm/ai` | Mỗi chu kỳ inference | speed, occupancy, queue, vehicle count |
| Tham số điều chỉnh runtime | Cấu hình AI Service | Khi triển khai/vận hành | `RUNTIME_MIN_GREEN`, `RUNTIME_MAX_GREEN`, `RUNTIME_GREEN_TIME_STEP` |

Quy tắc gọn nhất:

- Snapshot area chứa những gì ít thay đổi.
- Inference request chỉ chứa những gì thay đổi theo thời gian thực.
- Nếu một field có thể hydrate ổn định từ snapshot thì không gửi lại trong inference.

## 2. Input loại 1: đăng ký snapshot area

### 2.1 API nhận dữ liệu

```http
PUT /internal/sync/areas/{area_id}/real-network
```

Base URL local:

```text
http://localhost:8002
```

Snapshot dùng để AI Service:

- Lưu topology thực vào DB nội bộ.
- Compile `real_normalization.json`.
- Sinh `network.json`.
- Sinh config từng cross trong `intersections/cross_<cross_id>.json`.
- Ghép runtime bundle với mạng thực thông qua `tenantId + networkId + simToReal`.

### 2.2 Cấu trúc tổng

```json
{
  "sourceEventId": "real-network-1308700-20260603T093000-v17",
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "sourceVersion": "management-db-export-2026-06-03T09:30:00+07:00",
  "area": {},
  "areaCrosses": [],
  "crosses": [],
  "roads": [],
  "cycles": [],
  "stages": [],
  "simToReal": {}
}
```

### 2.3 Top-level fields

| Field | Required | Nguồn dữ liệu | Ghi chú |
|---|---:|---|---|
| `sourceEventId` | Có | Backend sync tự sinh | Idempotency key. Snapshot mới phải dùng ID mới |
| `tenantId` | Khuyến nghị mạnh | Cấu hình tích hợp | Phải khớp `tenant_id` trong model bundle |
| `networkId` | Khuyến nghị mạnh | Cấu hình tích hợp | Phải khớp `network_id` trong model bundle |
| `schemaVersion` | Có | Hằng số contract | Hiện dùng `real-network/v1` |
| `sourceVersion` | Khuyến nghị | Export job/version DB | Giúp trace snapshot được export từ đâu |
| `area` | Có | DB quản lý | Thông tin area |
| `areaCrosses` | Có | DB quản lý | Cross nào thuộc area, gắn cycle nào |
| `crosses` | Có | DB quản lý | Danh sách nút giao |
| `roads` | Có | DB quản lý | Danh sách road/approach |
| `cycles` | Có | DB quản lý | Cycle của từng cross |
| `stages` | Có | DB quản lý | Stage của từng cycle |
| `simToReal` | Có | UI/operator overlay | Mapping cross mô phỏng -> cross thực |

Nếu không gửi `tenantId`, service mặc định `default`. Nếu không gửi `networkId`, service mặc định `area_<area_id>`. Production nên gửi rõ cả hai để tránh bundle mô phỏng và snapshot thực không ghép được với nhau.

### 2.4 `area`

Thông tin định danh area thực.

```json
{
  "area_id": 1308700,
  "area_name": "Cologne3 Aachener Corridor",
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `area_id` | Có | Phải khớp `{area_id}` trên URL |
| `area_name` | Khuyến nghị | Tên để hiển thị/log |
| `is_active` | Khuyến nghị | `1`/`true` nếu area đang dùng |

Không cần gửi các field phục vụ UI thuần túy nếu AI Service không dùng để inference.

### 2.5 `areaCrosses`

Mapping area -> cross -> cycle.

```json
{
  "area_id": 1308700,
  "cross_id": 33000000101001,
  "cycle_id": 1001,
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `area_id` | Có | Area thực |
| `cross_id` | Có | Cross thực thuộc area |
| `cycle_id` | Có | Cycle đang áp dụng cho cross |
| `is_active` | Khuyến nghị | Chỉ active cross nên được đưa vào inference |

Một `cross_id` nên có đúng một `cycle_id` active tại một thời điểm.

### 2.6 `crosses`

Danh sách nút giao thực.

```json
{
  "id": 33000000101001,
  "location": "50.927821,6.928104",
  "old_id": "33202549",
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `id` | Có | Real cross ID |
| `location` | Khuyến nghị mạnh | Dạng `"lat,lon"`; giúp service suy direction ổn định từ GPS |
| `old_id` | Khuyến nghị | ID nguồn/legacy; hữu ích để trace và map với mô phỏng |
| `is_active` | Khuyến nghị | Chỉ active cross nên được runtime dùng |

Nếu không có GPS, service có thể dựa vào direction code trong road, nhưng rủi ro mismatch convention giữa khách hàng cao hơn.

### 2.7 `roads`

Danh sách road/approach trong area.

```json
{
  "id": 700001,
  "from_cross": 33000000101001,
  "from_cross_direction": 1,
  "to_cross": null,
  "to_cross_direction": null,
  "number_of_lanes": 1,
  "length": 120,
  "capacity_design": 1800,
  "speed_design": 50,
  "coordinates": [
    {
      "order_number": 1,
      "latitude": 50.92917,
      "longitude": 6.928104
    },
    {
      "order_number": 2,
      "latitude": 50.927821,
      "longitude": 6.928104
    }
  ],
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `id` | Có | Real road ID, dùng trong inference `roadId` |
| `from_cross` | Có | Cross mà road thuộc về/đi vào |
| `from_cross_direction` | Khuyến nghị | Direction từ DB nếu có |
| `to_cross` | Khuyến nghị nếu có | Cross kế tiếp nếu road nối sang nút khác |
| `to_cross_direction` | Tùy chọn | Direction tại cross kế tiếp |
| `number_of_lanes` | Khuyến nghị mạnh | Dùng cho capacity/density/feature |
| `length` | Khuyến nghị mạnh | Dùng để chuẩn hóa queue/density |
| `capacity_design` | Khuyến nghị mạnh | Dùng làm `saturationFlow` khi runtime không gửi |
| `speed_design` | Khuyến nghị | Dùng để chuẩn hóa traffic feature nếu cần |
| `coordinates` | Khuyến nghị mạnh | Polyline GPS, giúp suy direction và neighbor graph |
| `is_active` | Khuyến nghị | Chỉ active road nên được inference gửi demand |

Không gửi road không thuộc area hoặc road không thể map về cross active.

### 2.8 `cycles`

Cycle tĩnh của từng cross.

```json
{
  "id": 1001,
  "cross_id": 33000000101001,
  "cycle_length": 88,
  "yellow": 3,
  "red_clear": 1,
  "number_of_stages": 2,
  "cycle_type": 0,
  "old_id": "33202549-CK",
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `id` | Có | Real cycle ID |
| `cross_id` | Có | Cross sở hữu cycle |
| `cycle_length` | Có | Tổng chu kỳ, tính cả green + yellow + red-clear |
| `yellow` | Khuyến nghị mạnh | Default yellow nếu stage không có yellow riêng |
| `red_clear` | Khuyến nghị mạnh | Default all-red/red-clear; có thể bằng `0` nếu không có all-red |
| `number_of_stages` | Khuyến nghị | Dùng để validate stage count |
| `cycle_type` | Tùy chọn | Giữ nếu DB có |
| `old_id` | Tùy chọn | Trace ID nguồn |
| `is_active` | Khuyến nghị | Cycle đang dùng |

`cycle_length` phải thống nhất với stage:

```text
sum(stage.green + stage.yellow + stage.red_clear) ~= cycle_length
```

Nếu một nút không có all-red, gửi `red_clear = 0`. Không bỏ field chỉ vì giá trị bằng 0.

### 2.9 `stages`

Stage/phase tĩnh của từng cycle.

```json
{
  "id": 89101,
  "cycle_id": 1001,
  "order_number": 1,
  "green": 42,
  "yellow": 3,
  "red_clear": 1,
  "min_green_time": 15,
  "max_green_time": 80,
  "old_id": "0",
  "is_active": 1
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `id` | Có | Real stage ID, dùng trong inference `stageId` |
| `cycle_id` | Có | Cycle sở hữu stage |
| `order_number` | Có | Thứ tự stage trong cycle |
| `green` | Khuyến nghị mạnh | Green fixed-time/current default |
| `yellow` | Khuyến nghị mạnh | Yellow riêng của stage; fallback từ cycle nếu thiếu |
| `red_clear` | Khuyến nghị mạnh | Red-clear/all-red riêng của stage; fallback từ cycle nếu thiếu |
| `min_green_time` | Khuyến nghị | Guardrail min green |
| `max_green_time` | Khuyến nghị | Guardrail max green |
| `old_id` | Khuyến nghị nếu có model mapping | ID stage trong nguồn/mô phỏng |
| `is_active` | Khuyến nghị | Stage đang dùng |

Nếu stage có yellow/red-clear khác default cycle, phải gửi ở `stages[]`.

### 2.10 `simToReal`

Overlay mapping từ cross mô phỏng sang cross thực.

```json
{
  "33202549": 33000000101001,
  "360082": 33000000101002
}
```

| Thành phần | Required | Ghi chú |
|---|---:|---|
| Key | Có | Sim cross ID trong network/training bundle |
| Value | Có | Real cross ID, phải tồn tại trong `crosses[].id` |

`simToReal` không lấy trực tiếp từ DB quản lý. Đây là dữ liệu cấu hình overlay do UI/operator/integration team nhập hoặc xác nhận. Không nên auto-map theo thứ tự trong production nếu chưa được review.

### 2.11 Dữ liệu không nên đưa vào snapshot

| Dữ liệu | Lý do |
|---|---|
| Speed/occupancy/queue realtime | Đây là dữ liệu động, gửi ở inference |
| Output plan AI | Snapshot chỉ mô tả topology và plan fixed-time/default |
| Log/audit runtime | Lưu ở hệ thống audit, không thuộc topology |
| UI-only metadata không dùng cho điều khiển | Làm payload nặng và khó version |

## 3. Input loại 2: inference runtime

### 3.1 API nhận dữ liệu

```http
POST /api/algorithm/ai
```

Base URL local:

```text
http://localhost:8001
```

Inference input chỉ cần mô tả trạng thái hiện tại tại thời điểm gọi. AI Service sẽ hydrate phần tĩnh từ snapshot đã đăng ký.

### 3.2 Cấu trúc tổng

```json
{
  "areaId": 1308700,
  "timestamp": "2026-06-03T09:30:00+07:00",
  "crosses": []
}
```

### 3.3 Top-level fields

| Field | Required | Ghi chú |
|---|---:|---|
| `areaId` | Có | Area thực đã đăng ký snapshot và có runtime bundle active |
| `timestamp` | Khuyến nghị | Thời điểm dữ liệu sensor/TSC được lấy |
| `crosses` | Có | Danh sách cross cần inference, nên thuộc cùng một area |

Production nên gửi một area mỗi request. Không gom nhiều area vào một request.

### 3.4 `crosses[]`

Mỗi cross chứa trạng thái đèn hiện tại và demand của các road.

```json
{
  "crossId": 33000000101001,
  "cycleId": 1001,
  "stages": [],
  "roads": []
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `crossId` | Có | Real cross ID đã có trong snapshot |
| `cycleId` | Khuyến nghị | Cycle hiện tại đang chạy trên TSC |
| `stages` | Có | Stage hiện tại của cycle |
| `roads` | Có | Demand realtime theo road |

Không cần gửi `areaId` trong từng cross nếu đã có top-level `areaId`.

### 3.5 `stages[]`

Stage runtime chỉ cần ID và green hiện tại.

```json
{
  "stageId": 89101,
  "greenTime": 41
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `stageId` | Có | Real stage ID đã có trong snapshot |
| `greenTime` | Có | Green hiện tại/fixed-time hiện tại của stage |
| `duration` | Tùy chọn legacy | Nếu gửi, là `green + yellow + redClear` |

Không nên gửi `yellow`, `redClear`, `stageCode`, `oldId` trong inference production. Các field này đã nằm trong snapshot. Chỉ gửi override khi đang debug hoặc chạy legacy.

### 3.6 `roads[]`

Road runtime chỉ chứa demand realtime.

```json
{
  "roadId": 700001,
  "averageSpeed": 28.0,
  "occupancySpace": 40.0,
  "totalVehicle": 12,
  "queueLength": 15,
  "windowSeconds": 60
}
```

| Field | Required | Ghi chú |
|---|---:|---|
| `roadId` | Có | Real road ID đã có trong snapshot |
| `averageSpeed` | Có | Tốc độ trung bình trong window, mặc định đơn vị `km/h` |
| `averageSpeedUnit` | Tùy chọn legacy | Chỉ gửi nếu cần override sang `"m/s"`; nếu thiếu service mặc định `"km/h"` |
| `occupancySpace` | Có | Occupancy `0..100` |
| `totalVehicle` | Khuyến nghị | Số xe trong window |
| `queueLength` | Khuyến nghị | Mét; nếu `0..1` service hiểu là ratio khi biết road length |
| `windowSeconds` | Khuyến nghị | Độ dài window đo, ví dụ `60` |
| `density` | Tùy chọn | Gửi nếu sensor đã tính sẵn |

Nếu sensor không có `totalVehicle`, `queueLength`, hoặc `density`, vẫn có thể gửi `0`/`null` tùy field. Tuy nhiên tín hiệu traffic sẽ nghèo hơn. Tối thiểu runtime nên luôn có `averageSpeed` và `occupancySpace`.

### 3.7 Dữ liệu không nên gửi trong inference production

| Field | Lý do |
|---|---|
| `cycleLength` | Hydrate từ `cycles[].cycle_length` trong snapshot |
| `yellow`, `redClear` | Hydrate từ `stages[]` hoặc `cycles[]` trong snapshot |
| `direction` | Hydrate từ GPS/direction map trong real normalization |
| `saturationFlow` | Hydrate từ `capacity_design`/lane config |
| `toCrossId` | Hydrate từ topology road trong snapshot |
| `coordinates` | Dữ liệu tĩnh, đã có ở snapshot |
| `number_of_lanes`, `length`, `speed_design` | Dữ liệu tĩnh, đã có ở snapshot |
| `simToReal` | Chỉ dùng khi đăng ký snapshot/model mapping |

Gửi các field này mỗi chu kỳ không làm service tốt hơn nếu snapshot đã đúng; ngược lại có thể gây mismatch giữa DB tĩnh và dữ liệu runtime.

## 4. Quan hệ giữa hai input

| Inference field | Được validate/hydrate từ snapshot |
|---|---|
| `areaId` | `area.area_id`, URL `{area_id}` |
| `crossId` | `crosses[].id`, `areaCrosses[].cross_id` |
| `cycleId` | `areaCrosses[].cycle_id`, `cycles[].id` |
| `stageId` | `stages[].id` |
| `roadId` | `roads[].id` |
| `greenTime` | So với `min_green_time`, `max_green_time`, `cycle_length` |
| `averageSpeed` | Mặc định `km/h`; có thể dùng `speed_design` để chuẩn hóa |
| `queueLength` | Chuẩn hóa bằng `roads[].length` nếu gửi dạng ratio |
| `occupancySpace` | Dùng trực tiếp làm traffic feature |

Nếu inference báo thiếu static metadata, hãy kiểm tra snapshot trước, không thêm bừa static field vào runtime request.

## 5. Minimum valid payloads

### 5.1 Snapshot tối thiểu dùng được

```json
{
  "sourceEventId": "real-network-1308700-20260603T093000-v17",
  "tenantId": "default",
  "networkId": "cologne3",
  "schemaVersion": "real-network/v1",
  "area": {
    "area_id": 1308700,
    "area_name": "Area 1308700",
    "is_active": 1
  },
  "areaCrosses": [
    {
      "area_id": 1308700,
      "cross_id": 33000000101001,
      "cycle_id": 1001,
      "is_active": 1
    }
  ],
  "crosses": [
    {
      "id": 33000000101001,
      "location": "50.927821,6.928104",
      "is_active": 1
    }
  ],
  "roads": [
    {
      "id": 700001,
      "from_cross": 33000000101001,
      "number_of_lanes": 1,
      "length": 120,
      "capacity_design": 1800,
      "coordinates": [
        {
          "order_number": 1,
          "latitude": 50.92917,
          "longitude": 6.928104
        },
        {
          "order_number": 2,
          "latitude": 50.927821,
          "longitude": 6.928104
        }
      ],
      "is_active": 1
    }
  ],
  "cycles": [
    {
      "id": 1001,
      "cross_id": 33000000101001,
      "cycle_length": 88,
      "yellow": 3,
      "red_clear": 1,
      "number_of_stages": 2,
      "is_active": 1
    }
  ],
  "stages": [
    {
      "id": 89101,
      "cycle_id": 1001,
      "order_number": 1,
      "green": 40,
      "yellow": 3,
      "red_clear": 1,
      "min_green_time": 10,
      "max_green_time": 60,
      "is_active": 1
    },
    {
      "id": 89102,
      "cycle_id": 1001,
      "order_number": 2,
      "green": 40,
      "yellow": 3,
      "red_clear": 1,
      "min_green_time": 10,
      "max_green_time": 60,
      "is_active": 1
    }
  ],
  "simToReal": {
    "33202549": 33000000101001
  }
}
```

### 5.2 Inference tối thiểu dùng được

```json
{
  "areaId": 1308700,
  "timestamp": "2026-06-03T09:30:00+07:00",
  "crosses": [
    {
      "crossId": 33000000101001,
      "cycleId": 1001,
      "stages": [
        {
          "stageId": 89101,
          "greenTime": 40
        },
        {
          "stageId": 89102,
          "greenTime": 40
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

## 6. Checklist không thừa, không thiếu

### Snapshot area

| Check | Kỳ vọng |
|---|---|
| Có `tenantId`, `networkId` | Khớp model bundle |
| Có `simToReal` | Tất cả sim cross cần chạy map sang real cross |
| Có `cycle_length` | Mỗi cycle active có tổng chu kỳ |
| Có `yellow`, `red_clear` | Mỗi cycle/stage có intergreen rõ ràng, kể cả `0` |
| Có road static | `number_of_lanes`, `length`, `capacity_design` nếu DB có |
| Có GPS | `crosses[].location`, `roads[].coordinates` nếu DB có |
| Không có traffic realtime | Speed/occupancy/queue không nằm ở snapshot |

### Inference

| Check | Kỳ vọng |
|---|---|
| Có `areaId` | Đúng area đã sync |
| Có `crossId`, `cycleId` | Đúng topology hiện tại |
| Có `stageId`, `greenTime` | Đủ stage trong cycle đang chạy |
| Có `roadId` | Road thuộc cross trong snapshot |
| Có demand tối thiểu | `averageSpeed`, `occupancySpace` |
| Có demand khuyến nghị | `totalVehicle`, `queueLength`, `windowSeconds` |
| Không gửi topology tĩnh | Không gửi lại direction/cycleLength/yellow/redClear/coordinates nếu snapshot đã đầy đủ |

## 7. File ví dụ trong repo

| Loại input | File |
|---|---|
| Snapshot area | [api-payload-examples/register-real-network-snapshot.json](api-payload-examples/register-real-network-snapshot.json) |
| Inference compact | [api-payload-examples/inference-compact-request.json](api-payload-examples/inference-compact-request.json) |

Khi tạo payload thật, hãy thay `sourceEventId` placeholder bằng ID thật và đảm bảo `tenantId + networkId` khớp model bundle đang active.
