# Deployment — Vendor Cloud + Customer Edge

> Tài liệu này mô tả mô hình deploy production của AI service: vendor giữ **Sim Bundle** (build + push), khách hàng host Edge service (chỉ inference). Khi vendor có model mới → tự động deploy xuống tất cả Edge khách hàng mà không cần khách hàng làm gì.
>
> 👉 Trước khi deploy production, đã chạy thử pipeline local thành công qua [end-to-end-test.md](end-to-end-test.md). Hiểu pipeline tổng quát: [PIPELINE.md](PIPELINE.md).

## 1. Phân vai (vendor vs customer)

| Vai trò | Sở hữu / chịu trách nhiệm |
|---------|--------------------------|
| **Vendor** (bạn) | Train model, build **Sim Bundle** (`*.sim.zip`), push lên MinIO, host CI/CD pipeline, quản lý versioning |
| **Customer** (khách hàng) | Host Edge AI service (Docker), tích hợp Core Controller (Lớp 1), gửi inference request với `areaId` |

→ Customer **chỉ cần biết `areaId`** khi gọi `POST /api/algorithm/ai`. Vendor toàn quyền quyết định bundle nào đang serve cho area đó.

## 2. Mô hình deploy đề xuất: Edge + Auto-sync từ Vendor Cloud

```
┌──────────── VENDOR CLOUD ────────────────────┐
│                                               │
│  Service-ai (training) →                      │
│    scripts/build_sim_bundle.py → MinIO        │
│                                      ▲        │
│                                      │        │
│  bundles.vendor.com bucket layout:   │        │
│    sim/tenant_kh1/area_x/area_x.sim.zip │     │
│    sim/tenant_kh1/area_x/area_x-v2.sim.zip │  │
│    sim/tenant_kh2/area_y/area_y.sim.zip │     │
└──────────────────────────────────────┼────────┘
                                       │
                  outbound HTTPS only  │
                  (long-poll listener) │
              ┌────────────────────────┼──────────────┐
              │                        │              │
       ┌──────▼─────┐          ┌──────▼─────┐  ┌─────▼──────┐
       │ KH 1 EDGE  │          │ KH 2 EDGE  │  │ KH N EDGE  │
       │            │          │            │  │            │
       │ ai-ops     │          │ ai-ops     │  │ ai-ops     │
       │ ai-runtime │          │ ai-runtime │  │ ai-runtime │
       │ SQLite     │          │ SQLite     │  │ MySQL      │
       │ MinIO ?    │          │            │  │            │
       └──────┬─────┘          └──────┬─────┘  └────────────┘
              │ POST /api/algorithm/ai (intranet, <10ms)
              ▼
       ┌──────────────┐
       │Core Controller│  ← phần mềm khách hàng
       │+ Camera/Sensor│
       └──────────────┘
```

**Đặc điểm:**
- Latency inference < 10ms (intranet)
- Outbound only từ Edge → vendor MinIO (xuyên NAT/firewall)
- Vendor toàn quyền update model
- Customer zero-touch sau lần setup đầu

Lý do chọn mô hình này thay vì SaaS, image-baked, hay vendor-push: xem [docs/auto-sync.md](auto-sync.md#tại-sao-chọn-listener-thay-vì-webhook).

## 3. Vendor side — Setup 1 lần

### 3.1 Host Artifact Store (MinIO)

Bạn cần 1 instance MinIO public với HTTPS. Có thể tự host trên cloud provider (AWS, GCP, DigitalOcean) hoặc dùng managed S3.

**Bucket layout đề xuất (Sim Bundle):**
```
bundles.vendor.com/
├── sim/
│   ├── tenant_kh1/
│   │   ├── area_phuquoc/
│   │   │   ├── area_phuquoc.sim.zip
│   │   │   └── area_phuquoc-v2.sim.zip
│   │   └── area_dongnai/
│   │       └── area_dongnai.sim.zip
│   └── tenant_kh2/
│       └── ...
```

### 3.2 Tạo credentials per-customer (read-only theo prefix)

```bash
# Tạo policy chỉ cho phép đọc tenant_kh1/
cat > policy_kh1.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetBucketNotification"],
      "Resource": "arn:aws:s3:::bundles/sim/tenant_kh1/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::bundles",
      "Condition": {
        "StringLike": { "s3:prefix": ["sim/tenant_kh1/*"] }
      }
    }
  ]
}
EOF

mc alias set vendor https://bundles.vendor.com VENDOR_ROOT_KEY VENDOR_ROOT_SECRET
mc admin policy create vendor policy-tenant-kh1 policy_kh1.json
mc admin user add vendor kh1_access_key <strong_random_secret>
mc admin policy attach vendor policy-tenant-kh1 --user kh1_access_key
```

→ Customer kh1 chỉ list/read được prefix `tenant_kh1/`. Không thấy customer khác.

### 3.3 Setup CI/CD pipeline (Jenkins)

[Jenkinsfile](../Jenkinsfile) đã có sẵn các stage chính (sau refactor pipeline mới):

| Stage | Khi chạy | Output |
|---|---|---|
| Checkout, Lint, Test | Mọi build | — |
| Build Image | Mọi build | Docker image |
| Push Image | Branch `main` | Image push registry |

Configure Jenkins với credentials:
- `docker-registry` (username/password) — push Docker image

> ⚠️ **Quan trọng**: Pipeline service KHÔNG build sim bundle, KHÔNG build runtime bundle.
> - Sim bundle do training repo `Service-ai` build (`Service-ai/scripts/build_sim_bundle.py`) và upload lên MinIO. Có thể setup pipeline riêng bên repo Service-ai.
> - Runtime bundle do composer trong ai-ops tự sinh từ Sim Bundle + Real Network Snapshot trong DB customer.

→ Customer Edge sẽ tự pull sim bundle mới sau ~1-2s qua auto-sync listener (nếu listener đang chạy). Khi listener disable, có thể fallback bằng `POST /ops/sim-bundles/pull` của ai-ops.

### 3.4 Manual workflow

```powershell
# Vendor's machine - chạy trong repo training Service-ai
cd Service-ai

# Build Sim Bundle mới từ training outputs
.\.venv\Scripts\python.exe -X utf8 scripts\build_sim_bundle.py `
  --network area_phuquoc `
  --policy-onnx tmp\onnx_eval\policy.onnx `
  --policy-meta tmp\onnx_eval\policy_meta.json `
  --tenant-id tenant_kh1 `
  --network-id area_phuquoc `
  --version 1.2.0 `
  --output-zip dist\area_phuquoc.sim.zip

# Push lên vendor MinIO
docker run --rm `
  -v ${PWD}/dist:/data `
  --entrypoint /bin/sh `
  minio/mc:latest `
  -c "mc alias set vendor https://bundles.vendor.com VENDOR_KEY VENDOR_SECRET && \
      mc cp /data/area_phuquoc.sim.zip vendor/bundles/sim/tenant_kh1/area_phuquoc/area_phuquoc.sim.zip"

# Done. Edge kh1 sẽ tự deploy trong ~1-2s.
```

### 3.5 Monitoring all customers (vendor dashboard)

Tự host Prometheus federation kéo metrics từ tất cả Edge:

```yaml
# vendor-prometheus.yml
scrape_configs:
  - job_name: edge-customers
    metrics_path: /federate
    params:
      'match[]':
        - '{job=~"ai-runtime|ai-ops"}'
    static_configs:
      - targets:
        - kh1-edge.example.com:9090
        - kh2-edge.example.com:9090
```

Vendor Grafana dashboard hiển thị inference rate / drift events / active bundle version cho từng customer.

## 4. Customer side — Setup 1 lần

Vendor ship cho customer 1 package gồm:
- `customer-edge.zip` — Docker compose + configs
- 1 file `.env.<customer_id>` đã pre-fill credentials
- (Tuỳ chọn) `real_network_snapshot.json` mẫu để test sync API
- README ngắn với 3 bước cài

### 4.1 Cấu trúc package

```
customer-edge/
├── docker-compose.yml         # ai-runtime + ai-ops + sqlite
├── .env.kh1                   # config riêng (vendor pre-fill)
├── real_network_snapshot.json # optional, sample payload
└── README.md                  # 3 bước cài
```

### 4.2 File `.env.kh1` (vendor pre-fill)

```env
# Customer identity
TENANT_ID=tenant_kh1
NETWORK_ID=area_phuquoc

# Vendor Artifact Store (read-only credentials)
MINIO_ENABLED=true
MINIO_ENDPOINT=bundles.vendor.com
MINIO_ACCESS_KEY=kh1_access_key
MINIO_SECRET_KEY=<vendor cấp>
MINIO_BUCKET=bundles
MINIO_SECURE=true                        # production: true
MINIO_PREFIX=

# Auto-sync (key feature!)
MINIO_AUTO_SYNC_ENABLED=true
MINIO_AUTO_SYNC_PREFIX=sim/tenant_kh1/
MINIO_AUTO_SYNC_SUFFIX=.sim.zip
MINIO_AUTO_SYNC_AUTO_ACTIVATE=true
MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS=600
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_PREFIX=sim/tenant_kh1/
SIM_BUNDLE_SUFFIX=.sim.zip
SIM_BUNDLE_AUTO_ACTIVATE=true

# Local DB (SQLite cho gọn nhẹ)
DATABASE_URL=sqlite:///./ai_service.db

# Auth: Core Controller gọi inference
INTERNAL_API_KEY=<random per customer>

# Core Controller phải sync real network snapshot
# PUT /internal/sync/areas/{area_id}/real-network

# Strict mode
AI_STRICT_MODE=true                       # production: bật để fail-fast
STARTUP_PREFLIGHT=true

# Service role: dev có thể all, production tách 2 process
SERVICE_ROLE=all
```

### 4.3 Customer thực hiện (3 bước)

```powershell
# 1. Extract
Expand-Archive customer-edge.zip -DestinationPath C:\rlops-edge

# 2. (Optional) Edit .env nếu cần đổi gì
notepad C:\rlops-edge\.env.kh1

# 3. Start
cd C:\rlops-edge
docker compose --env-file .env.kh1 up -d
```

**Verify:**
```powershell
# Health
Invoke-RestMethod http://localhost:8001/health

# Auto-sync đang chạy
$h = @{ "X-Internal-API-Key" = "<key>" }
Invoke-RestMethod -Uri http://localhost:8002/ops/auto-sync/status -Headers $h
# enabled: true, listener.alive: true
```

### 4.4 Customer gọi inference

Core Controller chỉ cần biết `areaId`. Service tự dùng bundle đang active.

```http
POST http://<edge-host>:8001/api/algorithm/ai
Content-Type: application/json

{
  "crosses": [
    {
      "id": 1,
      "areaId": 1,
      ...
    }
  ],
  "cycleTime": 90
}
```

→ Trả về thời gian đèn xanh per stage, latency <10ms.

Chi tiết schema: [api-reference.md](api-reference.md) + [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

## 5. Vendor update model — workflow đầy đủ

```
Vendor train xong model mới
        │
        ▼
[1] Build Sim Bundle (Service-ai/scripts/build_sim_bundle.py)
        │
        ▼
[2] Push lên MinIO bucket vendor
  s3://bundles/sim/tenant_kh1/area_phuquoc/area_phuquoc.sim.zip
        │
        │   ~1-2 giây
        ▼
[3] Edge ai-ops listener nhận event s3:ObjectCreated
        │
        ▼
[4] Auto pull → validate → write active.json → notify ai-runtime
        │
        ▼
[5] ai-runtime preflight + reload policy
        │
        ▼
[6] Inference tiếp theo dùng bundle mới
        │
        ▼
[7] Vendor monitor qua Prometheus federation
    - inference_total tiếp tục
    - active_bundle_info gauge có version mới
    - drift_events_total bắt đầu accumulate baseline mới
```

**Rollback nhanh:**
```powershell
# Vendor gọi ai-ops của customer
$h = @{ "X-Internal-API-Key" = "<customer_key>" }
$body = @{ tenantId = "tenant_kh1" } | ConvertTo-Json
Invoke-RestMethod -Method POST `
  -Uri "https://kh1-edge.example.com:8002/ops/networks/area_phuquoc/rollback" `
  -Headers $h -ContentType "application/json" -Body $body
```

(Hoặc xóa file mới khỏi MinIO — nhưng vendor giữ lịch sử thì rollback API đẹp hơn.)

## 6. Multi-tenant trên cùng 1 Edge (advanced)

Một Edge có thể serve nhiều `area_id` cùng lúc nếu khách hàng có nhiều ngã tư.

`auto_sync` listener sẽ pull tất cả bundle dưới `MINIO_AUTO_SYNC_PREFIX`. Mỗi bundle có `network_id` riêng → service tự map area → bundle qua DB (`area_registry.network_id` ↔ `model_bundle.network_id`).

Ví dụ KH1 có 3 area:
```
MinIO bucket:
  sim/tenant_kh1/area_a/area_a.sim.zip
  sim/tenant_kh1/area_b/area_b.sim.zip
  sim/tenant_kh1/area_c/area_c.sim.zip

Edge:
  /app/models/networks/area_a/active.json → bundle_a
  /app/models/networks/area_b/active.json → bundle_b
  /app/models/networks/area_c/active.json → bundle_c

Inference request với areaId=2 (mapping → network_id=area_b)
  → service load bundle_b.policy.onnx → inference
```

## 7. So sánh với các mô hình khác

| Mô hình | Latency | Customer effort | Vendor control | Phù hợp khi |
|---------|---------|-----------------|----------------|-------------|
| **Edge + Auto-sync** | <10ms | Cài 1 lần | ✅ | **Mặc định cho ITS** |
| SaaS (vendor host inference) | 60-200ms | Zero | ✅ | Khách hàng nhỏ, accept internet |
| Edge + Vendor push (VPN) | <10ms | Cấp VPN | ✅ | Khách hàng có VPN sẵn |
| Image-baked | <10ms | `docker pull` mỗi update | ❌ Phụ thuộc customer | Model rất ít update |

## 8. Checklist deploy customer mới

- [ ] Vendor: tạo MinIO user + policy cho tenant mới
- [ ] Vendor: build **Sim Bundle** + push MinIO
- [ ] Customer/Controller: sync `real_network_snapshot` cho các area
- [ ] Vendor: tạo `.env.<customer_id>` với credentials
- [ ] Vendor: zip package → gửi customer
- [ ] Customer: extract + `docker compose up -d`
- [ ] Customer: verify `/health`, `/auto-sync/status`
- [ ] Customer: tích hợp Core Controller gọi `POST /api/algorithm/ai`
- [ ] Vendor: federate Prometheus từ Edge customer (cho dashboard global)

## 9. Bước tiếp theo

- [auto-sync.md](auto-sync.md) — chi tiết cơ chế auto-deploy
- [configuration.md](configuration.md) — env vars
- [troubleshooting.md](troubleshooting.md) — common issues khi deploy
