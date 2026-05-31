# Kế hoạch tích hợp & chạy thử với phần mềm điều khiển đèn thật

> Tài liệu này mô tả lộ trình đưa AI Algorithm Service từ môi trường dev sang **vận hành thật** trên phần cứng điều khiển đèn tín hiệu giao thông (Traffic Signal Controller — TSC) tại hiện trường, bao gồm cả phương án rollback, giám sát an toàn và tiêu chí go/no-go.
>
> **Đối tượng**: DevOps, integrator (đội tích hợp Lớp 1 — Core Controller), kỹ sư hiện trường, kỹ sư AI.
>
> 👉 Trước khi đọc file này, đảm bảo đã hiểu pipeline qua [PIPELINE.md](PIPELINE.md) và đã chạy thành công demo qua [end-to-end-test.md](end-to-end-test.md).

---

## 1. Tổng quan tích hợp

### 1.1 Mục tiêu

Đưa policy đã train (Model Bundle) vào điều phối đèn tín hiệu thật ở 1 → N ngã tư, với:

- **Độ trễ inference** ≤ 200 ms/chu kỳ (95th percentile).
- **Không vi phạm an toàn**: `minGreen ≤ phase.duration ≤ maxGreen`, không bỏ pha gây starvation.
- **Khả năng rollback** trong < 60 s về điều khiển fixed-time hoặc TRC mặc định.

### 1.2 Phạm vi tài liệu

| Có trong tài liệu | Không có trong tài liệu |
|---|---|
| Tích hợp API giữa Core Controller (Lớp 1) ↔ AI Service (Lớp 2) | Cấu hình PLC / cabinet vật lý |
| Lộ trình staging → shadow → pilot → production | Hợp đồng SLA với khách hàng |
| Kịch bản test trên hardware thật | Training pipeline (xem repo `Service-trainer`) |
| Rollback & monitoring | Audit ATGT (xem hồ sơ ATGT riêng) |

### 1.3 Vai trò các thành phần (Lớp theo `kientrucRLOps.pdf`)

```
┌─────────────────────────────────────────────────────────────┐
│  Lớp 0: Sensor / Camera / Inductive Loop  (thiết bị hiện trường)│
└────────────┬────────────────────────────────────────────────┘
             │ traffic counts, queue length
             ▼
┌─────────────────────────────────────────────────────────────┐
│  Lớp 1: Core Controller (phần mềm điều khiển đèn thật)      │
│  - Thu thập state từ field                                  │
│  - Gửi POST /api/algorithm/ai  (HTTP, JSON)                 │
│  - Nhận `algorithmOutputs` → đẩy plan xuống TSC qua NTCIP   │
│  - Có fallback nội bộ (fixed-time / TRC) khi AI fail        │
└────────────┬────────────────────────────────────────────────┘
             │ POST /api/algorithm/ai
             ▼
┌─────────────────────────────────────────────────────────────┐
│  Lớp 2: AI Algorithm Service (repo hiện tại)                │
│  - ai-runtime: inference ONNX, 6 lớp defense-in-depth       │
│  - ai-ops: bundle lifecycle, auto-sync                      │
└─────────────────────────────────────────────────────────────┘
```

**Hợp đồng giữa Lớp 1 ↔ Lớp 2**: REST API duy nhất `POST /api/algorithm/ai`. Lớp 1 chịu trách nhiệm hoàn toàn về việc actuate đèn — AI Service **chỉ đề xuất** thời lượng pha.

---

## 2. Pre-requisites

### 2.1 Hardware & mạng

| Hạng mục | Yêu cầu |
|---|---|
| Edge server (chạy AI Service) | 4 vCPU, 8 GB RAM, 50 GB SSD, có thể đặt cùng phòng máy với Core Controller |
| GPU | Không bắt buộc (ONNX CPU đủ cho ≤ 50 ngã tư) |
| Mạng nội bộ Lớp 1 ↔ Lớp 2 | Latency RTT < 20 ms, băng thông ≥ 10 Mbps |
| Internet outbound (cho ai-ops sync MinIO) | Cho phép HTTPS tới `*.minio.vendor.local` hoặc qua VPN |
| Đồng bộ thời gian | NTP, lệch ≤ 1 s giữa Lớp 1 / Lớp 2 / TSC |

### 2.2 Software phía Core Controller (Lớp 1) cần làm

1. **HTTP client** gọi `POST /api/algorithm/ai` với timeout 500 ms, retry tối đa 1 lần.
2. **Mapping schema**: chuyển trạng thái nội bộ → `AIInput` (xem [api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md)).
3. **Validation output**: kiểm tra `status == 1` và `sum(phase.duration) ≈ cycleLength` trước khi push xuống TSC.
4. **Fallback path**: khi AI Service trả lỗi hoặc timeout, dùng plan fixed-time đã cấu hình sẵn (không bao giờ để TSC mất plan).
5. **Audit log**: lưu cả input gửi đi + output nhận về với `requestId` (header `X-Request-Id`) để đối chiếu khi điều tra sự cố.

### 2.3 Dữ liệu & artifact cần chuẩn bị

Pipeline mới có **2 luồng song song**, controller chịu trách nhiệm cả 2 phía:

**Phía control service / backend (controller):**

- [ ] Backend export được snapshot từ DB quản lý theo schema ở [PIPELINE.md §4.2](PIPELINE.md). Bao gồm `area + areaCrosses + crosses + roads + cycles + stages + simToReal`.
- [ ] **Khuyến nghị**: payload chứa GPS — `crosses[].location` (string `"lat,lon"`) và `roads[].coordinates` (polyline kiểu `v_road_coordinate`). Khi có GPS, direction được suy bằng thuật toán GPI sao chép từ Service-ai → khử mọi rủi ro mismatch encoding direction giữa các customer. Chi tiết [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback).
- [ ] Nếu DB chỉ có `from_cross_direction` / `to_cross_direction` (không có GPS): xác nhận encoding dùng convention nào — service auto-detect 4-dir (1..4) vs 8-dir (0/2/4/6) per snapshot. Diagonal code (NE/SE/SW/NW = 1/3/5/7 trong 8-dir) sẽ bị drop chứ không map sang cardinal gần nhất.
- [ ] Gọi `PUT /internal/sync/areas/{id}/real-network` mỗi khi topology thay đổi. Service tự eager compile `real_normalization.json`.
- [ ] (Tuỳ chọn) Gọi `GET /internal/sync/areas/{id}/real-normalization` để verify chuẩn hoá đã sẵn sàng trước khi training upload sim bundle. Kiểm tra `direction_map` trong response — nếu cross nào có direction_map rỗng, composer sẽ raise `DIRECTION_MISSING_IN_REAL` lúc build runtime bundle.

**Phía training team (vendor):**

- [ ] Sim Bundle build xong từ training, đẩy lên MinIO theo layout `sim/{tenant}/{network}/{name}.sim.zip` (suffix `.sim.zip` bắt buộc).
- [ ] Manifest chứa `network_id` khớp với `networkId` mà controller dùng.
- [ ] `obs_stats.json` (mean/std normalization) đi kèm trong sim bundle, dùng làm baseline cho drift detector.

**Phía hiện trường:**

- [ ] Hành trình mẫu (canned scenarios) — 24 h dữ liệu giao thông quá khứ của khu vực mục tiêu, dùng để chạy shadow + validate phase 2.

### 2.4 Quyền & approval

| Item | Người duyệt |
|---|---|
| Triển khai shadow mode (không actuate) | Trưởng nhóm tích hợp |
| Triển khai pilot 1 ngã tư (có actuate, có người giám sát) | Trưởng phòng vận hành + Sở GTVT (nếu là dự án công) |
| Mở rộng > 5 ngã tư | Trưởng phòng vận hành + báo cáo Sở GTVT |
| Rollback khẩn cấp | Bất kỳ kỹ sư on-call (không cần duyệt) |

---

## 3. Lộ trình triển khai theo phase

### Phase 1 — Lab integration (1-2 tuần)

**Mục tiêu**: Core Controller gọi được AI Service trên môi trường lab, không có TSC thật.

**Thực hiện**:

1. Triển khai stack đầy đủ trong lab: `docker compose --profile app --profile db --profile storage up -d`.
2. Đẩy 1 bundle "synthetic" (train từ SUMO simulation) lên MinIO.
3. Core Controller gửi `test_cologne3_payload.json` định kỳ 5 s/lần, log toàn bộ output.
4. Verify:
   - [ ] Latency p95 ≤ 200 ms (xem Grafana panel "Inference Latency").
   - [ ] Output luôn pass guardrails (counter `ai_guardrail_violations_total` không tăng bất thường).
   - [ ] Core Controller parse được response, không crash.

**Tiêu chí go phase 2**: 24 h chạy liên tục, 0 lỗi 5xx, 0 panic trên cả 2 phía.

### Phase 2 — Shadow mode trên hiện trường (2-4 tuần)

**Mục tiêu**: AI Service nhận **dữ liệu thật** từ Core Controller nhưng output **chỉ log, không đẩy xuống TSC**. TSC vẫn chạy plan cũ (fixed-time / TRC).

**Thực hiện**:

1. Cài AI Service lên edge server đặt cạnh Core Controller (xem [deployment.md](deployment.md) cho mô hình customer edge).
2. Core Controller bật cờ `AI_SHADOW_MODE=true` ở phía mình — vẫn gọi AI Service mỗi chu kỳ, nhận output, nhưng **không apply**. Plan thực tế vẫn từ fixed-time.
3. So sánh offline: dump output AI vs plan thực mỗi chu kỳ, đẩy vào báo cáo hàng ngày.

**Metric cần theo dõi (Grafana)**:

| Metric | Ngưỡng cảnh báo |
|---|---|
| `ai_inference_total{status="success"}` | ≥ 99.5% / ngày |
| `ai_inference_latency_ms` p95 | ≤ 200 ms |
| `ai_guardrail_violations_total` (tốc độ tăng) | < 1% requests |
| `ai_drift_events_total` | ≤ 1/ngày, mỗi event phải có root cause |
| Lệch trung bình giữa green-time AI đề xuất vs plan thực | Có log để phân tích, không phải reject |

**Tiêu chí go phase 3**:
- [ ] ≥ 14 ngày shadow ổn định, không có incident.
- [ ] Đánh giá định tính bằng kỹ sư giao thông: "nếu apply, AI plan có hợp lý không?" → đồng thuận ≥ 80% các chu kỳ.
- [ ] Không có drift bất thường ở những khung giờ cao điểm.

### Phase 3 — Pilot 1 ngã tư có giám sát (4-8 tuần)

**Mục tiêu**: AI Service điều khiển thật 1 ngã tư duy nhất, có người trực hiện trường.

**Thực hiện**:

1. Chọn 1 ngã tư:
   - **Không** chọn ngã tư trục huyết mạch hoặc gần trường học/bệnh viện.
   - Có camera giám sát, có người trực tại tủ tín hiệu trong giờ giao thông cao điểm (16:30-19:00) 2 tuần đầu.
2. Phía Core Controller bật `AI_ACTUATE=true` chỉ cho `crossId` mục tiêu, các ngã tư khác vẫn chạy fixed-time.
3. Lịch giảm giám sát:
   - Tuần 1-2: trực hiện trường giờ cao điểm.
   - Tuần 3-4: trực từ xa qua camera + dashboard, có thể can thiệp manual qua Core Controller.
   - Tuần 5-8: theo dõi định kỳ, on-call.
4. **Nút "kill switch"** trên Core Controller: một thao tác → tắt actuation AI cho ngã tư đó, chuyển về fixed-time. Test kill switch đầu mỗi ca trực.

**Tiêu chí go phase 4**: xem [§6 Go/no-go](#6-tiêu-chí-gono-go-từng-phase).

### Phase 4 — Mở rộng (8+ tuần)

**Mục tiêu**: Mở rộng từng cụm 2-5 ngã tư mỗi tuần.

**Thực hiện**:

- Mỗi cụm mới: bundle riêng (network mới), preflight pass, shadow 7 ngày, pilot 7 ngày, full 7 ngày.
- Theo dõi tổng `numIntersections` đang AI-controlled trên dashboard.
- Mỗi cụm phải có baseline (fixed-time) để rollback.

---

## 4. API contract (chi tiết kỹ thuật)

### 4.1 Request

`POST http://ai-runtime:8000/api/algorithm/ai`

```json
{
  "crosses": [
    {
      "id": 1,
      "areaId": 1,
      "type": 1,
      "x": 105.8542,
      "y": 21.0285,
      "cycle": { "id": 1, "crossId": 1, "numberOfStages": 4, "cycleLength": 90, "isActive": 1, "...": "..." },
      "stages": [ { "id": 1, "duration": 30, "...": "..." } ],
      "roads": [ { "id": 11, "...": "..." } ]
    }
  ],
  "cycleTime": 90,
  "yellowTime": 3,
  "minGreen": 5,
  "maxGreen": 60,
  "greenTimeStep": 5
}
```

**Lưu ý quan trọng cho integrator**:

- `areaId` **bắt buộc**, AI Service dùng để route đến đúng policy ONNX. Sai `areaId` → lỗi 404.
- Một request có thể chứa nhiều `crosses` của **cùng 1 area** (mặc định `ENFORCE_SINGLE_AREA_PER_REQUEST=true`). Nếu muốn batch nhiều area trong 1 request, đặt env này về `false`.
- Schema đầy đủ: [src/schemas/ai_schemas/ai_input.py](../src/schemas/ai_schemas/ai_input.py) và các common schemas trong [src/schemas/common_schemas/](../src/schemas/common_schemas/).
- **Khuyến nghị dữ liệu road**: gửi `totalVehicle`, `windowSeconds`, `averageSpeedUnit` (mặc định `m/s`), `queueLength` và `occupancySpace` để runtime tính density từ flow/speed và chuẩn hoá gần với mô phỏng.

### 4.2 Response thành công

```json
{
  "status": 1,
  "numIntersections": 1,
  "areaIds": [1],
  "algorithmOutputs": [
    {
      "crossId": 1,
      "areaId": 1,
      "crossName": "Ngã tư X",
      "cycleId": 1,
      "cycleLength": 90,
      "phases": [
        { "stageId": 1, "duration": 32 },
        { "stageId": 2, "duration": 25 },
        { "stageId": 3, "duration": 30 },
        { "stageId": 4, "duration": 3 }
      ],
      "createdDate": "2026-05-12T10:23:00Z"
    }
  ]
}
```

**Core Controller phải validate trước khi push xuống TSC**:

1. `status == 1`.
2. `sum(phases[].duration) == cycleLength` (hoặc lệch ≤ 1 s do làm tròn).
3. Mỗi `duration` nằm trong `[minGreen, maxGreen]` của input.
4. `cycleLength` không lệch quá ±10% so với chu kỳ hiện tại đang chạy (tránh dao động đột ngột).

### 4.3 Response lỗi

Service luôn trả về JSON, **không 5xx im lặng**. Mọi lỗi đều có `code` để Core Controller phân loại:

```json
{
  "status": 0,
  "code": "POLICY_NOT_FOUND",
  "message": "No active bundle for areaId=1",
  "requestId": "req-abc123"
}
```

Mã lỗi đầy đủ: [src/core/error_codes.py](../src/core/error_codes.py). **Core Controller phải có nhánh xử lý cho từng `code`** — gợi ý mapping:

| `code` | Xử lý phía Core Controller |
|---|---|
| `POLICY_NOT_FOUND`, `BUNDLE_INVALID` | Fallback fixed-time + alert ops (cần redeploy bundle) |
| `INPUT_VALIDATION_FAILED` | Fallback fixed-time + log bug ở Lớp 1 (sai schema) |
| `GUARDRAIL_CLIPPED` | Apply output (đã clip an toàn) + log warning |
| `DRIFT_DETECTED` | Apply output nhưng tag chu kỳ, alert AI team |
| 5xx / timeout | Fallback fixed-time + retry sau N chu kỳ |

---

## 5. Test scenarios trên hardware thật

> Mỗi scenario chạy ở phase 3 (pilot, 1 ngã tư), không thực hiện ở phase 4 cho từng cụm mới mà chỉ chạy lại scenario rủi ro cao (5.3, 5.5).

### 5.1 Giờ cao điểm

- **Khi nào**: 7:00-9:00, 16:30-19:00 các ngày làm việc.
- **Quan sát**: queue length trên các nhánh, lượng phương tiện qua nút mỗi chu kỳ.
- **Tiêu chí pass**: throughput ≥ 95% baseline fixed-time, không phát sinh "phase starvation" (1 nhánh bị bỏ pha > 3 chu kỳ liên tiếp).

### 5.2 Giờ vắng (thấp tải)

- **Khi nào**: 23:00-5:00.
- **Quan sát**: phase nhỏ nhất không bị clip dưới `minGreen`.
- **Tiêu chí pass**: AI không "nhồi" thời lượng vào nhánh trống.

### 5.3 Sự kiện bất thường (mưa lớn, tai nạn, ngừng cấp điện 1 nhánh)

- **Khi nào**: ad-hoc khi xảy ra, có người giám sát chuyển sang fixed-time khi cần.
- **Tiêu chí**: AI không gây tình huống tệ hơn so với fixed-time; nếu drift detector trigger, có ghi log đầy đủ.

### 5.4 Drift test (chủ động)

- **Cách**: inject obs lệch baseline (tăng / giảm volume 50%) qua `test_payload_extreme.json` (xem [troubleshooting.md](troubleshooting.md)).
- **Tiêu chí pass**: `ai_drift_events_total` tăng, alert Grafana firing, AI Service không panic.

### 5.5 Failover test (kill switch)

- **Cách**: dừng container `ai-runtime` trong 30 s.
- **Tiêu chí pass**:
  - Core Controller chuyển sang fixed-time trong ≤ 1 chu kỳ.
  - Khi `ai-runtime` lên lại, Core Controller resume gọi AI (không cần manual reset).
  - Không có khoảng "đèn đứng" trên TSC.

### 5.6 Bundle hot-swap (no-downtime deploy)

- **Cách**: ai-ops auto-sync detect bundle version mới trên MinIO → activate → ai-runtime reload.
- **Tiêu chí pass**:
  - Inference không bị gián đoạn (0 request fail trong cửa sổ swap).
  - `BUNDLE_ACTIVE_INFO` gauge cập nhật đúng version mới trong ≤ 10 s.
  - Có thể rollback về version trước qua endpoint `/ops/networks/{net}/bundles/{old}/activate` (xem [api-reference.md](api-reference.md)).

---

## 6. Tiêu chí go/no-go từng phase

### Sau phase 1 (lab → shadow)

- [ ] 24 h chạy lab liên tục không lỗi 5xx.
- [ ] p95 latency ≤ 200 ms.
- [ ] Core Controller parse đúng output 100% requests.
- [ ] Test failover (5.5) pass.

### Sau phase 2 (shadow → pilot)

- [ ] ≥ 14 ngày shadow trên hiện trường.
- [ ] ≤ 0.5% requests fail (gồm cả timeout).
- [ ] Drift events đều có root cause được phân tích.
- [ ] Đánh giá định tính ≥ 80% chu kỳ "AI plan hợp lý".
- [ ] Kỹ sư giao thông + trưởng phòng vận hành ký duyệt.

### Sau phase 3 (pilot → mở rộng)

- [ ] ≥ 4 tuần pilot.
- [ ] Throughput ≥ 95% baseline (so sánh cùng khung giờ, cùng ngày trong tuần).
- [ ] 0 incident nghiêm trọng (định nghĩa: rollback khẩn không lên kế hoạch).
- [ ] Toàn bộ test 5.1-5.6 pass.
- [ ] Báo cáo gửi Sở GTVT (nếu là dự án công).

### Tiêu chí no-go (rollback ngay)

Bất kỳ điều kiện nào dưới đây → **kill switch về fixed-time** không cần duyệt:

- `ai_guardrail_violations_total` tăng > 5%/giờ.
- p95 latency > 500 ms trong 5 phút liên tiếp.
- Drift event high-severity (PSI > 0.5 trên nhiều feature).
- Báo cáo từ hiện trường: ùn tắc bất thường, đèn đứng > 5 s.
- 2 incident nghiêm trọng trong 24 h.

---

## 7. Safety mechanisms (đã có sẵn trong service)

Sáu lớp defense-in-depth, mô tả chi tiết ở [architecture.md](architecture.md):

1. **Input validation** (Pydantic) — reject schema sai trước khi vào inference.
2. **Preflight** ([src/runtime/preflight.py](../src/runtime/preflight.py)) — verify bundle khớp topology + obs_stats hợp lệ.
3. **Phase normalizer** ([src/preprocessing/phase_normalizer.py](../src/preprocessing/phase_normalizer.py)) — normalize cycle/phase trước inference, reject chu kỳ bất thường.
4. **Guardrails** ([src/runtime/guardrails.py](../src/runtime/guardrails.py)) — clip `duration` về `[minGreen, maxGreen]`, ép `sum == cycleLength`.
5. **Anti-starvation** ([src/runtime/starvation.py](../src/runtime/starvation.py)) — đếm số chu kỳ liên tiếp phase bị bỏ qua, force recovery sau `GUARDRAIL_ANTI_STARVATION_MAX_SKIPS` (mặc định 3).
6. **Drift detection** ([src/observability/drift.py](../src/observability/drift.py)) — PSI + KS test trên obs window, emit event nhưng **không tự rollback** (chính sách: alert + giữ chạy, ops quyết định).

> **Lưu ý**: AI Service không tự rollback bundle khi drift. Quyết định rollback là của Core Controller (kill switch) hoặc ops (gọi `/ops/.../activate` với version cũ).

---

## 8. Monitoring & rollback

### 8.1 Grafana dashboard chính

Provisioned tại [observability/grafana/provisioning/dashboards/rlops-overview.json](../observability/grafana/provisioning/dashboards/rlops-overview.json). Panel bắt buộc theo dõi trong giờ vận hành:

- **Inference rate + error rate** (theo `areaId`).
- **Latency p50/p95/p99**.
- **Guardrail violations** (theo `rule`).
- **Drift events** (theo `network_id`).
- **Active bundle info** (gauge `BUNDLE_ACTIVE_INFO`) — xác nhận đúng version đang phục vụ.
- **Auto-sync events** (poller + listener) — cần thấy heartbeat đều.

### 8.2 Alert rules (đề xuất)

| Alert | Điều kiện | Channel |
|---|---|---|
| AI service down | `up{job="ai-runtime"} == 0` ≥ 30 s | PagerDuty (P1) |
| High error rate | `rate(ai_inference_total{status!="success"}[5m]) > 0.05` | Slack #ai-ops (P2) |
| Latency spike | p95 > 500 ms trong 5 phút | Slack (P2) |
| Drift high severity | `ai_drift_events_total{severity="high"}` tăng | Slack (P3) |
| Auto-sync stalled | Không thấy `auto_sync_event_total` trong 30 phút | Slack (P3) |

### 8.3 Quy trình rollback

**Rollback Lớp 1 (kill switch)** — < 60 s:

1. Trên Core Controller console: tắt cờ `AI_ACTUATE` cho `crossId` mục tiêu.
2. TSC nhận plan fixed-time mặc định ngay chu kỳ tiếp theo.
3. Log incident.

**Rollback bundle (về version trước)** — < 5 phút:

```powershell
# Liệt kê bundle history
Invoke-WebRequest -Headers @{"X-Internal-API-Key"="..."} `
  http://ai-ops:8002/ops/networks/network_01/bundles | ConvertFrom-Json

# Activate version cũ
Invoke-WebRequest -Method POST -Headers @{"X-Internal-API-Key"="..."} `
  http://ai-ops:8002/ops/networks/network_01/bundles/v1.2.3/activate
```

ai-runtime sẽ hot-reload trong ≤ 5 s (poll active.json, TTL `ACTIVE_POINTER_TTL_SECONDS`).

**Rollback toàn service** — `docker compose --profile app down` rồi `up -d` với image version trước.

---

## 9. Checklist trước khi go-live mỗi phase

### Phase 1 → 2 (sang shadow)

- [ ] Stack đã chạy 24 h lab không lỗi
- [ ] Core Controller có nhánh fallback fixed-time
- [ ] Test failover (5.5) pass
- [ ] Audit log cả 2 phía bật, có `requestId` đối chiếu
- [ ] Grafana dashboard import được
- [ ] Alert rules đẩy vào Slack / PagerDuty

### Phase 2 → 3 (sang pilot có actuate)

- [ ] Báo cáo shadow 14 ngày được duyệt
- [ ] Chọn được 1 ngã tư phù hợp (không huyết mạch, có camera)
- [ ] Có người trực hiện trường giờ cao điểm
- [ ] Test kill switch hằng ngày, có log
- [ ] Bundle version pin (không auto-update trong pilot)
- [ ] Sở GTVT / chủ đầu tư ký duyệt (nếu cần)

### Phase 3 → 4 (mở rộng)

- [ ] Báo cáo pilot 4 tuần được duyệt
- [ ] Tất cả test 5.1-5.6 pass
- [ ] Throughput cải thiện ≥ baseline
- [ ] Quy trình deploy bundle theo cụm được viết SOP
- [ ] Đội on-call đủ người (24/7 cho 2 tuần đầu mỗi cụm mới)

---

## 10. Roles & responsibilities

| Vai trò | Trách nhiệm chính |
|---|---|
| **AI engineer** | Train + xuất bundle, hỗ trợ debug drift, định nghĩa metric quality |
| **Backend / integrator (Lớp 1)** | Implement HTTP client, validation, fallback, kill switch |
| **DevOps** | Deploy stack, observability, auto-sync, rollback bundle |
| **Kỹ sư giao thông** | Đánh giá định tính plan AI, xác nhận hợp lý hóa pha |
| **Kỹ sư hiện trường** | Trực ngã tư pilot, ghi nhận quan sát thực tế |
| **Trưởng phòng vận hành** | Ký duyệt từng phase, quyết định mở rộng |

---

## 11. Timeline tham khảo (đơn vị: tuần kể từ khi bundle đầu tiên sẵn sàng)

| Tuần | Hoạt động |
|---|---|
| 0-2 | Phase 1 — Lab integration |
| 3-6 | Phase 2 — Shadow trên hiện trường, ngã tư mục tiêu |
| 7-14 | Phase 3 — Pilot 1 ngã tư |
| 15+ | Phase 4 — Mở rộng cụm 2-5 ngã tư/tuần |

> Timeline có thể nén nếu shadow phát hiện ít vấn đề; **không khuyến nghị nén pilot** dưới 4 tuần vì cần quan sát đủ chu kỳ tuần (cao điểm, thấp điểm, cuối tuần, sự kiện bất thường).

---

## 12. Tham chiếu liên quan

- [end-to-end-test.md](end-to-end-test.md#01-quick-demo-10-phut-skip-race-conditionrollback) — demo nhanh.
- [architecture.md](architecture.md) — kiến trúc + mapping spec PDF.
- [deployment.md](deployment.md) — mô hình vendor cloud + customer edge.
- [auto-sync.md](auto-sync.md) — cơ chế auto-deploy bundle từ MinIO.
- [api-reference.md](api-reference.md) — chi tiết endpoint.
- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) — schema chi tiết của `POST /api/algorithm/ai`.
- [troubleshooting.md](troubleshooting.md) — debug khi tích hợp gặp lỗi.
