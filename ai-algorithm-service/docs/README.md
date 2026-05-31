# Tài liệu kỹ thuật — AI Algorithm Service

> 📌 **Bạn muốn test luồng hoạt động từ đầu đến cuối ngay bây giờ?** → đọc **[end-to-end-test.md](end-to-end-test.md)** và làm theo từng bước. ~15 phút sẽ thấy bundle active và inference chạy được.

---

## 🗺️ Mỗi file dùng vào việc gì?

### Nhóm 1: HIỂU pipeline (đọc trước khi làm)

| File | Mục đích | Khi nào đọc |
|---|---|---|
| **[PIPELINE.md](PIPELINE.md)** | Mô tả end-to-end pipeline (Sim Bundle → Real Snapshot → Runtime Bundle → Inference) bằng tiếng Việt, có sơ đồ, edge cases, FAQ | **Đọc đầu tiên** nếu bạn mới onboard |
| [architecture.md](architecture.md) | Kiến trúc nội bộ + mapping với spec gốc 4 lớp PDF | Khi cần hiểu code organization |
| [sim-to-real-mapping.md](sim-to-real-mapping.md) | Giải thích 3 không gian ID (sim / runtime standard / real DB) và cách composer map giữa chúng | Khi debug compatibility error |
| [elk-quickstart.md](elk-quickstart.md) | Hướng dẫn chạy ELK local de xem log | Khi can xem log bang Kibana |

### Nhóm 2: HÀNH ĐỘNG — chạy demo / test

| File | Mục đích | Thời gian |
|---|---|---|
| **[end-to-end-test.md](end-to-end-test.md)** ⭐ | **Test pipeline đầy đủ** từ đầu đến cuối, gồm: start stack, đăng ký real snapshot, build sim bundle, upload MinIO, verify active bundle, inference, **test race-condition**, rollback | ~15 phút |
| [testing.md](testing.md) | Cách chạy unit test (pytest) | — |

### Nhóm 3: TÍCH HỢP & DEPLOY

| File | Mục đích | Đối tượng |
|---|---|---|
| [integration-real-controller.md](integration-real-controller.md) | Lộ trình staging → shadow → pilot → production khi tích hợp với phần mềm điều khiển đèn thật | Integrator, kỹ sư hiện trường, DevOps |
| [deployment.md](deployment.md) | Mô hình vendor cloud + customer edge, multi-tenant, CI/CD setup | DevOps |
| [auto-sync.md](auto-sync.md) | Cơ chế auto-deploy bundle từ MinIO (listener + safety-net poller) | DevOps |

### Nhóm 4: REFERENCE — tra cứu khi cần

| File | Mục đích |
|---|---|
| [api-reference.md](api-reference.md) | Reference đầy đủ tất cả HTTP endpoint, request/response, error codes |
| [configuration.md](configuration.md) | Reference đầy đủ environment variables, profile dev/production |
| [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) | Chi tiết schema của `POST /api/algorithm/ai` (endpoint inference chính) |
| [../postman/README.md](../postman/README.md) | Cách dùng Postman collection có sẵn |

### Nhóm 5: DEBUG

| File | Mục đích |
|---|---|
| [troubleshooting.md](troubleshooting.md) | Common issues + cách debug — setup, sync, bundle deployment, inference, drift, observability |

---

## 🎯 Tôi nên đọc file nào?

### "Tôi mới onboard, chưa biết gì"

1. [PIPELINE.md](PIPELINE.md) — hiểu pipeline trước (10 phút đọc)
2. [end-to-end-test.md](end-to-end-test.md) — chạy thử thực tế (15 phút làm)
3. [architecture.md](architecture.md) — đọc khi cần hiểu code

### "Tôi muốn test demo từ đầu đến cuối ngay" ⭐

→ **[end-to-end-test.md](end-to-end-test.md)** — file này có 13 mục, bao gồm:
- Setup Docker
- Đăng ký Real Network Snapshot
- Build + Upload Sim Bundle
- Verify auto-compose
- Inference
- **Test race-condition** (Sim Bundle về trước Real Snapshot)
- Rollback
- Troubleshooting nhanh

### "Tôi cần demo nhanh cho khách / sếp xem"

→ [end-to-end-test.md](end-to-end-test.md#01-quick-demo-10-phut-skip-race-conditionrollback) — phần quick demo, chạy ~10 phút thấy kết quả.

### "Tôi đang viết Core Controller (Lớp 1) gọi AI Service"

1. [api-reference.md](api-reference.md) — biết các endpoint
2. [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) — schema chi tiết
3. [integration-real-controller.md](integration-real-controller.md) — kế hoạch tích hợp staging → production

### "Tôi đang deploy production cho khách hàng"

1. [deployment.md](deployment.md) — mô hình vendor cloud + customer edge
2. [auto-sync.md](auto-sync.md) — chi tiết cơ chế auto-deploy
3. [configuration.md](configuration.md) — env vars

### "Tôi đang debug lỗi"

→ [troubleshooting.md](troubleshooting.md) — có sẵn 10 categories common issues, bao gồm `DIRECTION_MISSING_IN_REAL` và inference lệch do direction sai (§3.9, §3.10)

### "Tôi muốn hiểu cách service suy direction N/E/S/W từ DB"

→ [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback) — thuật toán GPI (GPS-first, legacy auto-detect 4-dir/8-dir, fail loud khi thiếu data)

### "Tôi muốn tra cứu env var / endpoint"

- env vars → [configuration.md](configuration.md)
- endpoints → [api-reference.md](api-reference.md)

---

## ⭐ Thứ tự đọc đề xuất cho 3 personas

### 🧑‍💻 Developer mới gia nhập team

```
PIPELINE.md
  → end-to-end-test.md (chạy thử)
    → architecture.md
      → api-reference.md (khi cần)
        → testing.md (khi muốn viết test)
```

### 🔧 DevOps / SRE

```
PIPELINE.md
  → end-to-end-test.md
    → deployment.md
      → auto-sync.md
        → configuration.md
          → troubleshooting.md (bookmark!)
```

### 🚦 Integrator (làm Core Controller)

```
PIPELINE.md
  → api-reference.md
    → ../api_docs/run_ai_algorithm.md (schema chi tiết)
      → integration-real-controller.md (lộ trình staging → production)
```

---

## 📦 Spec gốc

[../kientrucRLOps.pdf](../kientrucRLOps.pdf) — kiến trúc 4 lớp đầy đủ. Service implement Lớp 2 + phần lớn Lớp 4.

## 📝 Quy ước trong docs

- **Code blocks**:
  - `bash` cho Linux/macOS
  - `powershell` cho Windows. Hầu hết command có cả 2 phiên bản.
- **Đường dẫn file** dùng markdown link để click được trong VSCode/IDE.
- **Lệnh `mc`** = MinIO Client, chạy trong Docker container `minio/mc:latest`.
- **`{{biến}}`** = biến cần thay theo môi trường thực tế (vd `{{customer_id}}`).
- **API key mặc định trong demo**: `sondinh2k3`. Production phải đổi.

---

## 🔥 Tóm tắt 1 dòng

> **Test pipeline từ đầu đến cuối → đọc [end-to-end-test.md](end-to-end-test.md). Hết.**
