# Kế hoạch tích hợp AI Algorithm Service với Phần mềm điều khiển đèn

> **Tài liệu kỹ thuật — Integration Plan**
>
> | Trường | Giá trị |
> |---|---|
> | Phiên bản | 1.0 |
> | Ngày phát hành | 2026-05-25 |
> | Tác giả | AI Team |
> | Đối tượng | DevOps, Backend integrator (đội phần mềm điều khiển đèn), AI Engineer, Trưởng phòng vận hành |
> | Trạng thái | Draft — chờ review |

---

## Mục lục

1. [Bối cảnh và phạm vi](#1-bối-cảnh-và-phạm-vi)
2. [Kiến trúc triển khai](#2-kiến-trúc-triển-khai)
3. [Phân chia trách nhiệm 2 phía](#3-phân-chia-trách-nhiệm-2-phía)
4. [Lộ trình triển khai theo phase](#4-lộ-trình-triển-khai-theo-phase)
5. [Pre-requisites — chuẩn bị hạ tầng](#5-pre-requisites--chuẩn-bị-hạ-tầng)
6. [Phase 1 — Deploy service lên server công ty](#6-phase-1--deploy-service-lên-server-công-ty)
7. [Phase 2 — Lab integration với phần mềm điều khiển](#7-phase-2--lab-integration-với-phần-mềm-điều-khiển)
8. [Phase 3 — Shadow mode trên hiện trường](#8-phase-3--shadow-mode-trên-hiện-trường)
9. [Phase 4 — Pilot 1 ngã tư có giám sát](#9-phase-4--pilot-1-ngã-tư-có-giám-sát)
10. [Phase 5 — Mở rộng](#10-phase-5--mở-rộng)
11. [API Contract](#11-api-contract)
12. [Monitoring, Alert, Rollback](#12-monitoring-alert-rollback)
13. [Checklist go/no-go từng phase](#13-checklist-gono-go-từng-phase)
14. [Timeline tham khảo](#14-timeline-tham-khảo)
15. [Phụ lục A — Lệnh và script chuẩn](#phụ-lục-a--lệnh-và-script-chuẩn)
16. [Phụ lục B — Vấn đề tồn đọng & đề xuất cải tiến](#phụ-lục-b--vấn-đề-tồn-đọng--đề-xuất-cải-tiến)

---

## 1. Bối cảnh và phạm vi

### 1.1 Bối cảnh

**Hiện trạng**: AI Algorithm Service đã hoàn chỉnh ~85% theo audit ngày 2026-05-25. Hiện service chỉ chạy thử trên máy cá nhân (Docker + Postman). Cần triển khai lên server công ty để phần mềm điều khiển đèn tích hợp.

**Mô hình triển khai mới**: Khác với mô hình "edge server đặt cạnh tủ điều khiển" trong tài liệu gốc [docs/integration-real-controller.md](ai-algorithm-service/docs/integration-real-controller.md), tài liệu này quy định:

- AI Service chạy **tập trung trên server của công ty** (không deploy lên edge device tại hiện trường).
- Phần mềm điều khiển đèn gọi AI Service qua **mạng nội bộ công ty** hoặc **VPN/mạng riêng** giữa 2 hệ thống.
- Một instance AI Service phục vụ **nhiều khu vực/khách hàng** (multi-tenant).

### 1.2 Mục tiêu của tài liệu

Hướng dẫn step-by-step để chuyển từ trạng thái "test local trên máy cá nhân" → "vận hành thật phục vụ phần mềm điều khiển đèn", bao gồm:

- Triển khai service lên server công ty (Docker production).
- Tích hợp với phần mềm điều khiển đèn — định nghĩa contract, error handling, fallback.
- Lộ trình go-live an toàn (lab → shadow → pilot → production).
- Monitoring, alert, rollback procedures.

### 1.3 Ngoài phạm vi

- Cấu hình PLC / cabinet vật lý (việc của đội phần mềm điều khiển đèn).
- Training pipeline (xem `Service-ai/`).
- SLA hợp đồng với khách hàng.

### 1.4 Vai trò các thành phần

```
┌─────────────────────────────────────────────────────────────────┐
│  Lớp 0: Sensor / Camera / Inductive Loop                        │
│  (thiết bị hiện trường, đo lưu lượng + chiều dài hàng đợi)      │
└────────────┬────────────────────────────────────────────────────┘
             │ traffic state
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Lớp 1: Phần mềm điều khiển đèn (TSC software, vendor partner)  │
│  - Thu thập state từ field                                      │
│  - HTTP client gọi AI Service                                   │
│  - Validate output → push xuống TSC qua NTCIP                   │
│  - Fallback fixed-time khi AI fail                              │
│  - Kill switch (cờ bật/tắt AI per cross)                        │
└────────────┬────────────────────────────────────────────────────┘
             │ HTTPS: POST /api/algorithm/ai
             │ (mạng nội bộ công ty / VPN)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Lớp 2: AI Algorithm Service (chạy trên server công ty)         │
│  ┌──────────────┐  ┌─────────────┐  ┌──────────┐  ┌──────────┐ │
│  │  ai-runtime  │  │   ai-ops    │  │  MySQL   │  │  MinIO   │ │
│  │  (inference) │  │  (lifecycle)│  │  (audit) │  │ (bundle) │ │
│  │  :8001       │  │  :8002      │  │  :3306   │  │  :9000   │ │
│  └──────────────┘  └─────────────┘  └──────────┘  └──────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**Hợp đồng giữa Lớp 1 ↔ Lớp 2**: REST API qua HTTPS, JSON, không state. AI Service **chỉ đề xuất** thời lượng pha; phần mềm điều khiển đèn chịu trách nhiệm hoàn toàn về việc actuate đèn.

---

## 2. Kiến trúc triển khai

### 2.1 Topology mạng

```
┌─────────────────────────────────────────────────────────────────┐
│                        Mạng nội bộ công ty                       │
│                                                                  │
│  ┌─────────────────────┐         ┌─────────────────────┐        │
│  │ Server AI Service   │◄────────│  Server phần mềm    │        │
│  │ (Linux, 4 vCPU,     │  HTTPS  │  điều khiển đèn     │        │
│  │  8GB RAM, 100GB SSD)│   API   │  (vendor partner)   │        │
│  │                     │  call   │                     │        │
│  │  - ai-runtime:8001  │         │  - HTTP client      │        │
│  │  - ai-ops:8002      │         │  - Audit log        │        │
│  │  - MySQL:3306       │         │  - Kill switch UI   │        │
│  │  - MinIO:9000       │         │                     │        │
│  │  - Nginx:443 (TLS)  │         │                     │        │
│  └─────────────────────┘         └─────────────────────┘        │
│           ▲                                                      │
│           │                                                      │
│  ┌────────┴──────────┐                                           │
│  │  AI Engineer      │                                           │
│  │  (upload bundle)  │                                           │
│  └───────────────────┘                                           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Cấu hình server công ty

| Hạng mục | Yêu cầu tối thiểu | Khuyến nghị |
|---|---|---|
| CPU | 4 vCPU | 8 vCPU |
| RAM | 8 GB | 16 GB |
| Disk | 100 GB SSD | 200 GB SSD |
| OS | Ubuntu 22.04 LTS / RHEL 8+ | Ubuntu 22.04 LTS |
| Docker | 24.0+ | 24.0+ |
| Docker Compose | v2.20+ | v2.20+ |
| Network | 1 Gbps NIC, IP cố định | 1 Gbps NIC, IP cố định + DNS A record |
| Backup | Snapshot hàng đêm | Snapshot + offsite backup |

### 2.3 Mạng giữa AI Service ↔ phần mềm điều khiển đèn

| Yêu cầu | Giá trị |
|---|---|
| Latency RTT | ≤ 20 ms (cùng datacenter) hoặc ≤ 50 ms (qua VPN) |
| Băng thông | ≥ 100 Mbps |
| Giao thức | HTTPS (TLS 1.2+) |
| Authentication | API Key (header `X-Internal-API-Key` cho sync API) |
| Firewall inbound | Chỉ cho phép IP của phần mềm điều khiển đèn → AI Service :443 |
| Firewall outbound | AI Service → MinIO (nội bộ), AI Service không cần truy cập internet trực tiếp |

---

## 3. Phân chia trách nhiệm 2 phía

### 3.1 Trách nhiệm của AI Team (bạn)

#### Hạ tầng
- [ ] Yêu cầu IT cấp server công ty đúng spec §2.2.
- [ ] Cài Docker + Docker Compose trên server.
- [ ] Cấu hình firewall + IP whitelist.
- [ ] Cài đặt nginx làm reverse proxy + TLS termination.
- [ ] Cấp domain/IP cố định cho service.
- [ ] Setup NTP sync.

#### Service
- [ ] Build & push Docker image của AI Service vào registry nội bộ.
- [ ] Deploy stack qua Docker Compose.
- [ ] Sinh & cấp API key (`X-Internal-API-Key`) cho phần mềm điều khiển đèn.
- [ ] Upload sim bundle (model đã train) lên MinIO.

#### Vận hành
- [ ] Setup Prometheus + Grafana + alert rules.
- [ ] Định nghĩa SOP rollback bundle.
- [ ] Lên lịch on-call rotation.
- [ ] Backup MySQL định kỳ.

#### Tài liệu
- [ ] Bàn giao API contract document (file này + Swagger nếu có).
- [ ] Cung cấp Postman collection ([ai-algorithm-service/postman/](ai-algorithm-service/postman/)).
- [ ] Bảng error codes + cách xử lý.
- [ ] Kênh liên lạc khi sự cố (Slack/email/hotline).

### 3.2 Trách nhiệm của đội phần mềm điều khiển đèn

#### Code phía họ phải viết
- [ ] HTTP client gọi `POST /api/algorithm/ai` với timeout 500ms, retry tối đa 1 lần.
- [ ] Mapping schema: chuyển trạng thái nội bộ → `AIInput` format ([api_docs/run_ai_algorithm.md](ai-algorithm-service/api_docs/run_ai_algorithm.md)).
- [ ] Validate response:
    - `status == 1`
    - `sum(phases[].greenTime + yellowTime + redClearTime) ≈ cycleLength` (lệch ≤ 1s do làm tròn)
    - Mỗi `greenTime` nằm trong `[minGreen, maxGreen]` của input
    - `cycleLength` không lệch quá ±10% so với chu kỳ hiện tại đang chạy
- [ ] **Fallback fixed-time** khi AI Service trả lỗi hoặc timeout. Đèn KHÔNG ĐƯỢC PHÉP đứng.
- [ ] **Kill switch UI**: cờ bật/tắt AI cho từng `crossId`. Khi tắt → dùng plan fixed-time cấu hình sẵn.
- [ ] **Audit log**: lưu cả request + response với `X-Request-Id` (header) để đối chiếu khi điều tra sự cố.
- [ ] Push real-network snapshot qua `PUT /internal/sync/areas/{id}/real-network` mỗi khi topology thay đổi.
- [ ] Handle 7 mã lỗi theo bảng mapping ở §11.4.

#### Khẳng định trước khi tích hợp

Đội phần mềm điều khiển đèn phải xác nhận họ đã implement đủ 8 điểm trên **trước khi** AI Team cho phép gọi production. Có thể dùng [Phụ lục A.5](#a5-checklist-cho-đội-phần-mềm-điều-khiển-đèn) làm checklist xác nhận.

### 3.3 Trách nhiệm chung (làm với nhau)

- [ ] Thống nhất schema input (đặc biệt: encoding direction 4-dir vs 8-dir, có GPS hay không).
- [ ] Test integration trên môi trường staging với payload thực tế.
- [ ] Test failover scenario (kill `ai-runtime` → controller fallback).
- [ ] Định nghĩa quy trình điều tra sự cố (ai chịu trách nhiệm khi đèn lỗi).

---

## 4. Lộ trình triển khai theo phase

```
┌─────────┐   ┌─────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐
│ Phase 1 │ → │   Phase 2   │ → │   Phase 3    │ → │  Phase 4   │ → │ Phase 5  │
│ Deploy  │   │     Lab     │   │  Shadow Mode │   │   Pilot    │   │ Mở rộng  │
│ Server  │   │ Integration │   │ (hiện trường)│   │ 1 ngã tư   │   │ N ngã tư │
└─────────┘   └─────────────┘   └──────────────┘   └────────────┘   └──────────┘
 1-2 tuần       1-2 tuần           2-4 tuần          4-8 tuần        8+ tuần
```

| Phase | Mục tiêu | Tiêu chí pass |
|---|---|---|
| 1 | Deploy AI Service lên server công ty, sẵn sàng cho gọi API | Service chạy 24h không lỗi, health endpoint OK, TLS hoạt động |
| 2 | Phần mềm điều khiển gọi được AI Service từ môi trường lab/staging | 24h gọi liên tục, 0 lỗi 5xx, p95 latency ≤ 200ms |
| 3 | Lấy dữ liệu thật từ hiện trường, AI chỉ log không actuate | ≥ 14 ngày shadow, đánh giá định tính ≥ 80% chu kỳ hợp lý |
| 4 | AI điều khiển thật 1 ngã tư có giám sát | ≥ 4 tuần pilot, throughput ≥ 95% baseline, 0 incident |
| 5 | Mở rộng 2-5 ngã tư/tuần | Mỗi cụm: shadow 7 ngày + pilot 7 ngày + full 7 ngày |

---

## 5. Pre-requisites — chuẩn bị hạ tầng

### 5.1 Server công ty

#### 5.1.1 Yêu cầu IT cấp server

Soạn ticket gửi IT với spec:

```
Subject: [AI-Service] Yêu cầu cấp server cho AI Algorithm Service

- OS: Ubuntu 22.04 LTS
- CPU: 8 vCPU
- RAM: 16 GB
- Disk: 200 GB SSD
- Network: 1 Gbps NIC, IP nội bộ cố định
- DNS: yêu cầu A record (ví dụ: ai-service.internal.company.local)
- Mở port: 443 inbound từ IP của phần mềm điều khiển đèn
- SSH key: <public key của AI Team>
- Backup: snapshot hàng đêm
- Mục đích: chạy AI Service phục vụ phần mềm điều khiển đèn giao thông
- Liên hệ: <tên + email AI Team>
```

#### 5.1.2 Cài Docker

Sau khi nhận server, SSH vào và cài:

```bash
# Cài Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Cài Docker Compose plugin (v2)
sudo apt-get update
sudo apt-get install -y docker-compose-plugin

# Verify
docker --version
docker compose version

# Add user vào group docker (logout/login để effect)
sudo usermod -aG docker $USER

# Setup NTP
sudo apt-get install -y chrony
sudo systemctl enable --now chrony
chronyc tracking
```

### 5.2 Cấp domain / IP cố định

Yêu cầu IT cấp DNS A record nội bộ:

```
ai-service.internal.company.local → <IP server>
```

Hoặc nếu không có DNS nội bộ, dùng IP trực tiếp và ghi rõ cho đội phần mềm điều khiển đèn.

### 5.3 TLS certificate

**Lựa chọn**:
- **Option A** (khuyến nghị): xin IT cấp cert từ CA nội bộ công ty.
- **Option B**: dùng Let's Encrypt nếu domain public.
- **Option C**: self-signed cert (CHỈ dùng cho staging, KHÔNG dùng production).

Đặt cert vào:

```
/opt/ai-service/certs/
├── fullchain.pem
└── privkey.pem
```

### 5.4 API key

Sinh API key mạnh (≥ 32 ký tự) cho `X-Internal-API-Key`:

```bash
openssl rand -hex 32
# Ví dụ output: a1b2c3d4...
```

Lưu key vào file `.env` của service (chi tiết §6.4), KHÔNG commit lên git.

Đưa key cho đội phần mềm điều khiển đèn qua kênh an toàn (1Password / vault / mật khẩu file ZIP).

### 5.5 Network firewall

Yêu cầu IT mở rule:

| Source | Destination | Port | Giao thức | Mục đích |
|---|---|---|---|---|
| IP phần mềm điều khiển đèn | Server AI Service | 443 | TCP | API call HTTPS |
| AI Team (SSH bastion) | Server AI Service | 22 | TCP | Quản trị |
| Server AI Service | Internal DNS | 53 | UDP | DNS resolution |
| Server AI Service | NTP server | 123 | UDP | Time sync |

**Mặc định DENY tất cả**, chỉ ALLOW các rule trên.

---

## 6. Phase 1 — Deploy service lên server công ty

### 6.1 Mục tiêu

Đưa AI Service từ "chạy local trên máy cá nhân" lên "chạy ổn định trên server công ty" với:
- HTTPS endpoint
- API key authentication
- Persistent storage cho MySQL + MinIO
- Health monitoring

### 6.2 Build & push Docker image

Trên máy dev:

```bash
cd /home/sondinh2k3/Documents/Working_ITS/RL_algo_for_ITS_Service/ai-algorithm-service

# Build image
docker build -t registry.internal.company.local/ai-service:v1.0.0 \
    -f Dockerfile ..

# Push lên registry nội bộ
docker push registry.internal.company.local/ai-service:v1.0.0

# Tag thêm latest
docker tag registry.internal.company.local/ai-service:v1.0.0 \
    registry.internal.company.local/ai-service:latest
docker push registry.internal.company.local/ai-service:latest
```

> **Lưu ý**: Nếu công ty chưa có registry nội bộ, có thể dùng Docker Hub private repo hoặc export image qua `docker save` + `scp` + `docker load`.

### 6.3 Copy code lên server

```bash
# Từ máy dev, copy thư mục lên server
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='*.db' \
    /home/sondinh2k3/Documents/Working_ITS/RL_algo_for_ITS_Service/ \
    deploy@ai-service.internal.company.local:/opt/ai-service/
```

### 6.4 Tạo file `.env.production`

Trên server, tại `/opt/ai-service/ai-algorithm-service/.env.production`:

```bash
# ============================================================
# Production environment
# ============================================================
APP_ENV=production
LOG_LEVEL=INFO
SERVICE_NAME=ai-algorithm-service

# ============================================================
# Service role (mặc định chạy combined trong cùng compose,
# nhưng phân ra 2 container ai-runtime + ai-ops)
# ============================================================
# Không cần set SERVICE_ROLE ở đây — đã set trong docker-compose

# ============================================================
# Bundle & Model
# ============================================================
MODEL_DIR=/app/models
BUNDLE_LAYOUT_ENABLED=true
AI_STRICT_MODE=true                # bật strict cho production
STARTUP_PREFLIGHT=true

# ============================================================
# MinIO
# ============================================================
MINIO_ENABLED=true
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=<sinh-random>
MINIO_SECRET_KEY=<sinh-random>
MINIO_BUCKET=ai-models
MINIO_SECURE=false                 # trong mạng nội bộ docker, không cần TLS
MINIO_PREFIX=models

MINIO_AUTO_SYNC_ENABLED=true
MINIO_AUTO_SYNC_PREFIX=sim/default/
MINIO_AUTO_SYNC_SUFFIX=.sim.zip
MINIO_AUTO_SYNC_AUTO_ACTIVATE=true
MINIO_AUTO_SYNC_POLL_INTERVAL_SECONDS=600

SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_PREFIX=sim/default/
SIM_BUNDLE_SUFFIX=.sim.zip
SIM_BUNDLE_AUTO_ACTIVATE=true

# ============================================================
# Database
# ============================================================
DATABASE_URL=mysql+pymysql://ai_user:<strong-password>@mysql:3306/ai_service

# ============================================================
# Internal API Key (CẤP CHO PHẦN MỀM ĐIỀU KHIỂN ĐÈN)
# ============================================================
INTERNAL_API_KEY=<API key sinh từ §5.4>

# ============================================================
# Guardrails
# ============================================================
GUARDRAIL_ANTI_STARVATION_MAX_SKIPS=3
ENFORCE_SINGLE_AREA_PER_REQUEST=true

# ============================================================
# Telemetry
# ============================================================
PROMETHEUS_ENABLED=true
DRIFT_DETECTION_ENABLED=true
DRIFT_PSI_THRESHOLD=0.2

# ============================================================
# Runtime <-> Ops communication
# ============================================================
RUNTIME_INTERNAL_URL=http://ai-runtime:8000
```

> **CRITICAL**: file `.env.production` chứa secret. Chmod 600 và KHÔNG commit lên git.

```bash
chmod 600 /opt/ai-service/ai-algorithm-service/.env.production
```

### 6.5 Cấu hình Docker Compose production

Tạo file `/opt/ai-service/ai-algorithm-service/docker-compose.production.yml`:

```yaml
services:
  mysql:
    image: mysql:8.0
    container_name: ai_mysql_prod
    command:
      - --default-authentication-plugin=mysql_native_password
    environment:
      MYSQL_ROOT_PASSWORD_FILE: /run/secrets/mysql_root_password
      MYSQL_DATABASE: ai_service
      MYSQL_USER: ai_user
      MYSQL_PASSWORD_FILE: /run/secrets/mysql_user_password
      TZ: UTC
    volumes:
      - mysql_data_prod:/var/lib/mysql
      - ./db/init/00_create_schema.sql:/docker-entrypoint-initdb.d/00_create_schema.sql:ro
    secrets:
      - mysql_root_password
      - mysql_user_password
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1"]
      interval: 10s
      timeout: 5s
      retries: 20
    restart: always
    networks:
      - ai_internal

  minio:
    image: minio/minio:latest
    container_name: ai_minio_prod
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER_FILE: /run/secrets/minio_root_user
      MINIO_ROOT_PASSWORD_FILE: /run/secrets/minio_root_password
      TZ: UTC
    volumes:
      - minio_data_prod:/data
    secrets:
      - minio_root_user
      - minio_root_password
    healthcheck:
      test: ["CMD-SHELL", "mc ready local || curl -fsS http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: always
    networks:
      - ai_internal

  ai-runtime:
    image: registry.internal.company.local/ai-service:v1.0.0
    container_name: ai_runtime_prod
    command:
      - "uv"
      - "run"
      - "uvicorn"
      - "src.main:app"
      - "--host"
      - "0.0.0.0"
      - "--port"
      - "8000"
      - "--workers"
      - "4"
    env_file:
      - .env.production
    environment:
      SERVICE_ROLE: runtime
    volumes:
      - model_storage_prod:/app/models
    depends_on:
      mysql:
        condition: service_healthy
      minio:
        condition: service_started
    restart: always
    networks:
      - ai_internal

  ai-ops:
    image: registry.internal.company.local/ai-service:v1.0.0
    container_name: ai_ops_prod
    command:
      - "uv"
      - "run"
      - "uvicorn"
      - "src.main:app"
      - "--host"
      - "0.0.0.0"
      - "--port"
      - "8002"
    env_file:
      - .env.production
    environment:
      SERVICE_ROLE: ops
    volumes:
      - model_storage_prod:/app/models
    depends_on:
      mysql:
        condition: service_healthy
      minio:
        condition: service_started
      ai-runtime:
        condition: service_started
    restart: always
    networks:
      - ai_internal

  nginx:
    image: nginx:alpine
    container_name: ai_nginx_prod
    ports:
      - "443:443"
      - "80:80"     # redirect 80 → 443
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/nginx.conf:ro
      - /opt/ai-service/certs:/etc/nginx/certs:ro
    depends_on:
      - ai-runtime
      - ai-ops
    restart: always
    networks:
      - ai_internal

  prometheus:
    image: prom/prometheus:latest
    container_name: ai_prometheus_prod
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=90d"
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./observability/prometheus-alerts.yml:/etc/prometheus/prometheus-alerts.yml:ro
      - prometheus_data_prod:/prometheus
    restart: always
    networks:
      - ai_internal

  grafana:
    image: grafana/grafana:latest
    container_name: ai_grafana_prod
    environment:
      GF_SECURITY_ADMIN_USER_FILE: /run/secrets/grafana_admin_user
      GF_SECURITY_ADMIN_PASSWORD_FILE: /run/secrets/grafana_admin_password
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - grafana_data_prod:/var/lib/grafana
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
    secrets:
      - grafana_admin_user
      - grafana_admin_password
    depends_on:
      - prometheus
    restart: always
    networks:
      - ai_internal

volumes:
  mysql_data_prod:
  minio_data_prod:
  model_storage_prod:
  prometheus_data_prod:
  grafana_data_prod:

secrets:
  mysql_root_password:
    file: ./secrets/mysql_root_password.txt
  mysql_user_password:
    file: ./secrets/mysql_user_password.txt
  minio_root_user:
    file: ./secrets/minio_root_user.txt
  minio_root_password:
    file: ./secrets/minio_root_password.txt
  grafana_admin_user:
    file: ./secrets/grafana_admin_user.txt
  grafana_admin_password:
    file: ./secrets/grafana_admin_password.txt

networks:
  ai_internal:
    driver: bridge
```

### 6.6 Cấu hình Nginx (reverse proxy + TLS)

Tạo `/opt/ai-service/ai-algorithm-service/deploy/nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    # Logging
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for" '
                    'rid=$http_x_request_id rt=$request_time';
    access_log /var/log/nginx/access.log main;
    error_log /var/log/nginx/error.log warn;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api_limit:10m rate=100r/s;
    limit_req_zone $binary_remote_addr zone=sync_limit:10m rate=10r/s;

    # Timeout
    proxy_connect_timeout 5s;
    proxy_send_timeout 10s;
    proxy_read_timeout 10s;

    # Upstream
    upstream ai_runtime {
        server ai-runtime:8000;
        keepalive 32;
    }

    upstream ai_ops {
        server ai-ops:8002;
        keepalive 16;
    }

    # HTTP → HTTPS redirect
    server {
        listen 80;
        server_name ai-service.internal.company.local;
        return 301 https://$server_name$request_uri;
    }

    # HTTPS server
    server {
        listen 443 ssl http2;
        server_name ai-service.internal.company.local;

        ssl_certificate /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;

        # Hide server version
        server_tokens off;

        # Inference endpoint - cao tần suất
        location /api/algorithm/ai {
            limit_req zone=api_limit burst=20 nodelay;
            proxy_pass http://ai_runtime;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Request-Id $http_x_request_id;
            proxy_set_header Connection "";
        }

        # Readiness / health endpoints
        location ~ ^/(health|ready)$ {
            proxy_pass http://ai_runtime;
            access_log off;
        }

        # Internal sync API (controller backend gọi)
        location /internal/sync/ {
            limit_req zone=sync_limit burst=5 nodelay;
            proxy_pass http://ai_ops;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }

        # Ops API (chỉ AI Team dùng — có thể thêm IP whitelist tại đây)
        location /ops/ {
            # allow <AI Team IP range>;
            # deny all;
            proxy_pass http://ai_ops;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
        }

        # Metrics (chỉ Prometheus scrape - whitelist IP)
        location /metrics {
            allow 172.16.0.0/12;   # Docker internal
            deny all;
            proxy_pass http://ai_runtime;
        }
    }
}
```

### 6.7 Tạo secrets

```bash
cd /opt/ai-service/ai-algorithm-service
mkdir -p secrets
chmod 700 secrets

# Tạo từng secret
openssl rand -hex 16 > secrets/mysql_root_password.txt
openssl rand -hex 16 > secrets/mysql_user_password.txt
echo "minioadmin_prod" > secrets/minio_root_user.txt
openssl rand -hex 16 > secrets/minio_root_password.txt
echo "ai_admin" > secrets/grafana_admin_user.txt
openssl rand -hex 16 > secrets/grafana_admin_password.txt

chmod 600 secrets/*.txt
```

### 6.8 Khởi động stack

```bash
cd /opt/ai-service/ai-algorithm-service

# Pull image (nếu dùng registry)
docker compose -f docker-compose.production.yml pull

# Up stack
docker compose -f docker-compose.production.yml up -d

# Verify
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs -f --tail=100
```

### 6.9 Verify deployment

```bash
# Health check (qua nginx + TLS)
curl -k https://ai-service.internal.company.local/health
# Expected: {"status": "ok", "role": "runtime"}

# Readiness
curl -k https://ai-service.internal.company.local/ready

# Test sync API với API key
curl -k -X PUT https://ai-service.internal.company.local/internal/sync/areas/1 \
    -H "X-Internal-API-Key: <your-api-key>" \
    -H "Content-Type: application/json" \
    -d '{
      "areaName": "Test Area",
      "isActive": 1,
      "controllerVisible": 1,
      "tenantId": "default",
      "networkId": "grid4x4",
      "sourceEventId": "init-001"
    }'
```

### 6.10 Upload model bundle

Build sim bundle ở phía training, upload lên MinIO:

```bash
# Từ máy training, copy bundle lên server
scp dist/grid4x4-v1.0.0.sim.zip deploy@ai-service.internal.company.local:/tmp/

# Trên server, upload qua mc
docker run --rm --network ai_internal \
    -v /tmp:/data \
    --entrypoint /bin/sh \
    minio/mc:latest \
    -c "mc alias set local http://minio:9000 minioadmin_prod \$(cat /run/secrets/minio_root_password) && \
        mc cp /data/grid4x4-v1.0.0.sim.zip \
        local/ai-models/sim/default/grid4x4/grid4x4-v1.0.0.sim.zip"
```

Verify auto-compose:
```bash
docker compose -f docker-compose.production.yml logs ai-ops | grep -E "sim-bundle|compose|active"
```

### 6.11 Tiêu chí Phase 1 pass

- [ ] Stack up đầy đủ 6 containers (mysql, minio, ai-runtime, ai-ops, nginx, prometheus, grafana).
- [ ] HTTPS endpoint trả 200 cho `/health`, `/ready`.
- [ ] Test gọi `/internal/sync/areas/1` với API key thành công.
- [ ] Upload bundle → auto-compose → activate thành công.
- [ ] Service chạy 24h liên tục không restart bất thường.
- [ ] Backup MySQL chạy thành công ít nhất 1 lần.

---

## 7. Phase 2 — Lab integration với phần mềm điều khiển

### 7.1 Mục tiêu

Đội phần mềm điều khiển đèn implement HTTP client + gọi được AI Service từ môi trường lab/staging của họ.

### 7.2 Bàn giao cho đội phần mềm điều khiển đèn

Email/document chứa:

1. **URL endpoint**: `https://ai-service.internal.company.local`
2. **API Key**: gửi qua kênh an toàn (1Password, vault)
3. **API contract**: file [api_docs/run_ai_algorithm.md](ai-algorithm-service/api_docs/run_ai_algorithm.md)
4. **Postman collection**: zip thư mục [postman/](ai-algorithm-service/postman/)
5. **Sample payload**: [test_cologne3_payload.json](ai-algorithm-service/test_cologne3_payload.json), [test_payload.json](ai-algorithm-service/test_payload.json)
6. **Error code mapping**: bảng §11.4 của tài liệu này
7. **Checklist xác nhận**: [Phụ lục A.5](#a5-checklist-cho-đội-phần-mềm-điều-khiển-đèn)

### 7.3 Quy trình test integration

#### 7.3.1 Test "smoke" — gọi được 1 request

Đội phần mềm điều khiển đèn gửi test request (đã register area 1 ở Phase 1):

```bash
curl -X POST https://ai-service.internal.company.local/api/algorithm/ai \
    -H "Content-Type: application/json" \
    -H "X-Request-Id: $(uuidgen)" \
    -d @test_payload.json
```

Expected: status 200, response JSON đúng schema.

#### 7.3.2 Test register topology

Đội phần mềm điều khiển đèn gọi sync API push topology thật:

```bash
curl -X PUT https://ai-service.internal.company.local/internal/sync/areas/1/real-network \
    -H "X-Internal-API-Key: <key>" \
    -H "Content-Type: application/json" \
    -d @real_network_snapshot.json
```

Verify:
```bash
curl -k https://ai-service.internal.company.local/api/algorithm/ai/areas/1/readiness
# Expected: { "ready": true, "hasPolicy": true, "hasNetwork": true, ... }
```

#### 7.3.3 Test load — 24h continuous

Script ở máy đội phần mềm điều khiển đèn, gọi mỗi 30s liên tục 24 giờ:

```bash
while true; do
    curl -X POST https://ai-service.internal.company.local/api/algorithm/ai \
        -H "Content-Type: application/json" \
        -H "X-Request-Id: $(uuidgen)" \
        -d @test_payload.json \
        -o /dev/null -s -w "%{http_code} %{time_total}s\n" \
        >> /tmp/ai_test.log
    sleep 30
done
```

Verify cuối ngày:
- ≥ 2880 requests
- ≥ 99.5% status 200
- p95 latency ≤ 200ms

#### 7.3.4 Test failover

AI Team chủ động dừng `ai-runtime`:

```bash
docker compose -f docker-compose.production.yml stop ai-runtime
sleep 60
docker compose -f docker-compose.production.yml start ai-runtime
```

Đội phần mềm điều khiển đèn verify:
- Trong 60s đó, họ chuyển sang fixed-time đúng cách (không có khoảng đèn đứng).
- Khi service lên lại, họ resume gọi AI tự động (không cần manual reset).

### 7.4 Tiêu chí Phase 2 pass

- [ ] Test smoke (7.3.1) pass.
- [ ] Test register topology (7.3.2) pass.
- [ ] Test 24h load (7.3.3) đạt:
    - ≥ 99.5% success rate
    - p95 latency ≤ 200ms
    - 0 lỗi 5xx
- [ ] Test failover (7.3.4) pass.
- [ ] Đội phần mềm điều khiển đèn ký xác nhận checklist [Phụ lục A.5](#a5-checklist-cho-đội-phần-mềm-điều-khiển-đèn).
- [ ] Đồng thuận thời điểm bắt đầu Phase 3.

---

## 8. Phase 3 — Shadow mode trên hiện trường

### 8.1 Mục tiêu

AI Service nhận **dữ liệu thật từ field** qua phần mềm điều khiển đèn, output **chỉ log, không actuate**. TSC vẫn chạy plan cũ (fixed-time / TRC mặc định).

### 8.2 Thực hiện

1. Phần mềm điều khiển đèn bật cờ `AI_SHADOW_MODE=true` ở phía họ — vẫn gọi AI Service mỗi chu kỳ, nhận output, nhưng **không apply**.
2. Plan thực tế vẫn từ fixed-time.
3. AI Service log đầy đủ vào `inference_audit` table.
4. Mỗi ngày: dump output AI vs plan thực, đưa vào báo cáo so sánh.

### 8.3 Metric cần theo dõi (Grafana)

| Metric | Ngưỡng cảnh báo |
|---|---|
| `ai_inference_total{status="success"}` | ≥ 99.5% / ngày |
| `ai_inference_latency_ms` p95 | ≤ 200 ms |
| `ai_guardrail_violations_total` (rate) | < 1% requests |
| `ai_drift_events_total` | ≤ 1/ngày, mỗi event phải có root cause |
| Lệch trung bình green-time AI vs plan thực | Log để phân tích |

### 8.4 Đánh giá định tính

Mỗi tuần, kỹ sư giao thông review một sample (~50 chu kỳ) — đánh giá: "nếu apply, AI plan có hợp lý không?".

Tiêu chí: ≥ 80% chu kỳ "hợp lý".

### 8.5 Tiêu chí Phase 3 pass

- [ ] ≥ 14 ngày shadow ổn định.
- [ ] ≤ 0.5% requests fail.
- [ ] Drift events đều có root cause.
- [ ] Đánh giá định tính ≥ 80% "AI plan hợp lý".
- [ ] Kỹ sư giao thông + trưởng phòng vận hành ký duyệt.

---

## 9. Phase 4 — Pilot 1 ngã tư có giám sát

### 9.1 Mục tiêu

AI Service điều khiển thật 1 ngã tư duy nhất, có người trực hiện trường.

### 9.2 Chọn ngã tư pilot

- **Không** chọn trục huyết mạch, gần trường học/bệnh viện.
- Có camera giám sát.
- Có người trực tại tủ tín hiệu trong giờ cao điểm (16:30-19:00) trong 2 tuần đầu.

### 9.3 Lịch giảm giám sát

| Tuần | Mức giám sát |
|---|---|
| 1-2 | Trực hiện trường giờ cao điểm |
| 3-4 | Trực từ xa qua camera + dashboard |
| 5-8 | Theo dõi định kỳ, on-call |

### 9.4 Kill switch

Đội phần mềm điều khiển đèn phải có UI/CLI để tắt AI cho `crossId` cụ thể trong **< 60 giây**. Test kill switch đầu mỗi ca trực.

### 9.5 Test scenarios

| # | Tên | Thực hiện khi |
|---|---|---|
| 5.1 | Giờ cao điểm | 7-9h, 16:30-19h ngày làm việc |
| 5.2 | Giờ vắng | 23h-5h |
| 5.3 | Sự kiện bất thường | Mưa lớn, tai nạn, ngừng điện 1 nhánh |
| 5.4 | Drift test | Chủ động inject obs lệch baseline |
| 5.5 | Failover test | Dừng `ai-runtime` 30s |
| 5.6 | Bundle hot-swap | Deploy model mới qua MinIO |

### 9.6 Tiêu chí Phase 4 pass

- [ ] ≥ 4 tuần pilot.
- [ ] Throughput ≥ 95% baseline (so cùng khung giờ, cùng ngày tuần).
- [ ] 0 incident nghiêm trọng (định nghĩa: rollback khẩn không lên kế hoạch).
- [ ] Toàn bộ test 5.1-5.6 pass.
- [ ] Báo cáo gửi Sở GTVT (nếu là dự án công).

---

## 10. Phase 5 — Mở rộng

### 10.1 Tốc độ mở rộng

Mỗi cụm mới: shadow 7 ngày → pilot 7 ngày → full 7 ngày.

Tốc độ tối đa: **2-5 ngã tư/tuần**.

### 10.2 Yêu cầu mỗi cụm

- Bundle riêng cho từng network (nếu topology khác).
- Preflight pass.
- Có baseline fixed-time để rollback.

---

## 11. API Contract

> Đầy đủ ở [api_docs/run_ai_algorithm.md](ai-algorithm-service/api_docs/run_ai_algorithm.md). Phần này tóm tắt cho integrator.

### 11.1 Endpoint chính

```
POST https://ai-service.internal.company.local/api/algorithm/ai
Content-Type: application/json
X-Request-Id: <uuid>   # optional
```

Timeout phía client: **500ms**, retry tối đa 1 lần.

### 11.2 Endpoint sync (backend của họ gọi)

```
PUT https://ai-service.internal.company.local/internal/sync/areas/{id}/real-network
X-Internal-API-Key: <key>
Content-Type: application/json
```

Gọi mỗi khi topology thay đổi.

### 11.3 Endpoint phụ trợ

| Endpoint | Mục đích |
|---|---|
| `GET /health` | Liveness probe |
| `GET /ready` | Readiness probe |
| `GET /api/algorithm/ai/areas` | List area đang phục vụ |
| `GET /api/algorithm/ai/areas/{id}/readiness` | Detail readiness 1 area |

### 11.4 Error code mapping

| `errorCode` | HTTP | Xử lý phía phần mềm điều khiển đèn |
|---|---|---|
| `POLICY_NOT_FOUND`, `BUNDLE_INVALID` | 404 | Fallback fixed-time + alert AI Team |
| `INVALID_INPUT` | 400 | Fallback fixed-time + log bug phía họ |
| `MULTIPLE_AREAS_NOT_ALLOWED` | 400 | Fix code: chia request theo từng area |
| `AREA_NOT_FOUND` | 404 | Kiểm tra đã sync area chưa |
| `AREA_NOT_READY` | 409 | Fallback fixed-time + alert AI Team |
| `CONFIG_NOT_FOUND` | 404 | Fallback fixed-time + alert AI Team |
| `GUARDRAIL_CLIPPED` (warning) | 200 | Apply output (đã clip an toàn) + log warning |
| `DRIFT_DETECTED` (warning) | 200 | Apply output nhưng tag chu kỳ + alert AI Team |
| 5xx / timeout | — | Fallback fixed-time + retry sau N chu kỳ |

---

## 12. Monitoring, Alert, Rollback

### 12.1 Grafana dashboard

URL: `https://ai-service.internal.company.local:3000` (chỉ AI Team truy cập).

Panels bắt buộc:
- Inference rate + error rate (theo `areaId`)
- Latency p50/p95/p99
- Guardrail violations (theo `rule`)
- Drift events
- Active bundle info
- Auto-sync events

### 12.2 Alert rules

Tạo file `/opt/ai-service/ai-algorithm-service/observability/prometheus-alerts.yml`:

```yaml
groups:
  - name: ai_service_critical
    interval: 30s
    rules:
      - alert: AIServiceDown
        expr: up{job="ai-runtime"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "AI Service runtime down"
          description: "ai-runtime container không trả heartbeat trong 30s"

      - alert: HighErrorRate
        expr: rate(ai_inference_total{status!="success"}[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "AI inference error rate > 5%"

      - alert: LatencySpike
        expr: histogram_quantile(0.95, ai_inference_latency_ms_bucket) > 500
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "AI inference p95 latency > 500ms trong 5 phút"

      - alert: DriftHighSeverity
        expr: increase(ai_drift_events_total{severity="high"}[10m]) > 0
        labels:
          severity: warning
        annotations:
          summary: "Drift severity high detected"

      - alert: AutoSyncStalled
        expr: time() - max(auto_sync_event_total) > 1800
        for: 30m
        labels:
          severity: info
        annotations:
          summary: "Auto-sync không có event trong 30 phút"
```

Channel: PagerDuty cho critical, Slack/email cho warning.

### 12.3 Rollback procedures

#### 12.3.1 Rollback bundle (về version trước)

```bash
# Liệt kê bundle history
curl -H "X-Internal-API-Key: <key>" \
    https://ai-service.internal.company.local/ops/networks/grid4x4/bundles

# Activate version cũ
curl -X POST -H "X-Internal-API-Key: <key>" \
    -H "Content-Type: application/json" \
    -d '{"bundleId": "sim-grid4x4-OLD"}' \
    https://ai-service.internal.company.local/ops/networks/grid4x4/activate
```

`ai-runtime` hot-reload trong ≤ 5s.

#### 12.3.2 Rollback service (về image version trước)

```bash
cd /opt/ai-service/ai-algorithm-service

# Edit docker-compose.production.yml, đổi tag image về version cũ
# Hoặc dùng env var:
export AI_IMAGE_TAG=v0.9.0
docker compose -f docker-compose.production.yml up -d ai-runtime ai-ops
```

#### 12.3.3 Kill switch toàn service (khẩn cấp)

Đội phần mềm điều khiển đèn:
```
Tắt cờ AI_ACTUATE cho tất cả crossId → toàn bộ chuyển fixed-time.
```

AI Team:
```bash
docker compose -f docker-compose.production.yml stop ai-runtime
```

Phần mềm điều khiển đèn sẽ tự fallback (vì timeout/refused connection).

### 12.4 Backup

#### 12.4.1 MySQL

Cron job hàng đêm trên server:

```bash
# /etc/cron.daily/ai-mysql-backup
#!/bin/bash
BACKUP_DIR=/opt/ai-service/backups/mysql
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)
docker exec ai_mysql_prod mysqldump -uroot -p$(cat /opt/ai-service/ai-algorithm-service/secrets/mysql_root_password.txt) \
    ai_service > $BACKUP_DIR/ai_service_$DATE.sql
gzip $BACKUP_DIR/ai_service_$DATE.sql

# Giữ 30 ngày
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete
```

#### 12.4.2 MinIO bundles

MinIO data đã persistent qua volume. Snapshot volume hàng tuần qua công cụ của IT.

---

## 13. Checklist go/no-go từng phase

### 13.1 Phase 1 → 2 (deploy → integrate)

- [ ] Stack chạy 24h không lỗi
- [ ] HTTPS hoạt động đúng, cert hợp lệ
- [ ] API key đã cấp cho đội phần mềm điều khiển đèn
- [ ] Bundle đầu tiên upload + activate thành công
- [ ] Backup MySQL chạy ít nhất 1 lần
- [ ] Grafana + alert rules deploy xong
- [ ] Tài liệu API contract đã gửi đầy đủ

### 13.2 Phase 2 → 3 (integrate → shadow)

- [ ] 24h test load pass
- [ ] Test failover (7.3.4) pass
- [ ] Đội phần mềm điều khiển đèn ký checklist [A.5](#a5-checklist-cho-đội-phần-mềm-điều-khiển-đèn)
- [ ] Đồng thuận thời điểm shadow

### 13.3 Phase 3 → 4 (shadow → pilot)

- [ ] ≥ 14 ngày shadow
- [ ] Đánh giá định tính ≥ 80%
- [ ] Báo cáo shadow được duyệt
- [ ] Chọn được ngã tư phù hợp
- [ ] Có người trực hiện trường
- [ ] Test kill switch hằng ngày, có log
- [ ] Bundle version pin (không auto-update)
- [ ] Sở GTVT ký duyệt (nếu là dự án công)

### 13.4 Phase 4 → 5 (pilot → mở rộng)

- [ ] Báo cáo pilot 4 tuần được duyệt
- [ ] Test 5.1-5.6 pass
- [ ] Throughput cải thiện ≥ baseline
- [ ] SOP deploy bundle theo cụm
- [ ] On-call 24/7 đủ người cho 2 tuần đầu mỗi cụm

### 13.5 Tiêu chí no-go (rollback ngay)

Bất kỳ điều kiện nào → **kill switch về fixed-time** không cần duyệt:

- `ai_guardrail_violations_total` tăng > 5%/giờ
- p95 latency > 500ms trong 5 phút liên tiếp
- Drift event high-severity (PSI > 0.5 trên nhiều feature)
- Báo cáo từ hiện trường: ùn tắc bất thường, đèn đứng > 5s
- 2 incident nghiêm trọng trong 24h

---

## 14. Timeline tham khảo

| Tuần | Hoạt động | Bên thực hiện |
|---|---|---|
| 0-1 | Pre-requisites: cấp server, domain, cert, firewall | AI Team + IT |
| 2 | Phase 1 deploy stack, verify health | AI Team |
| 3-4 | Phase 2 lab integration | AI Team + đội phần mềm điều khiển đèn |
| 5-8 | Phase 3 shadow mode | Đội phần mềm điều khiển đèn + AI Team monitor |
| 9-16 | Phase 4 pilot 1 ngã tư | Tất cả + kỹ sư hiện trường |
| 17+ | Phase 5 mở rộng theo cụm | Tất cả |

**Tổng**: ~17 tuần (4 tháng) từ deploy đến mở rộng được cụm đầu tiên.

---

## Phụ lục A — Lệnh và script chuẩn

### A.1 Tạo backup MySQL

```bash
docker exec ai_mysql_prod mysqldump -uroot -p<pwd> ai_service \
    | gzip > /opt/ai-service/backups/mysql/ai_service_$(date +%Y%m%d).sql.gz
```

### A.2 Restore MySQL từ backup

```bash
gunzip -c /opt/ai-service/backups/mysql/ai_service_20260525.sql.gz \
    | docker exec -i ai_mysql_prod mysql -uroot -p<pwd> ai_service
```

### A.3 Tail log

```bash
# All services
docker compose -f docker-compose.production.yml logs -f --tail=100

# Specific
docker compose -f docker-compose.production.yml logs -f ai-runtime --tail=200
```

### A.4 Restart 1 service

```bash
docker compose -f docker-compose.production.yml restart ai-runtime
```

### A.5 Checklist cho đội phần mềm điều khiển đèn

Yêu cầu đội phần mềm điều khiển đèn xác nhận đầy đủ trước Phase 3:

```
[ ] Đã implement HTTP client gọi POST /api/algorithm/ai
[ ] Timeout client = 500ms, retry tối đa 1 lần
[ ] Đã implement mapping schema nội bộ → AIInput
[ ] Đã validate response trước khi push xuống TSC:
    [ ] status == 1
    [ ] sum(phases) ≈ cycleLength
    [ ] greenTime ∈ [minGreen, maxGreen]
    [ ] cycleLength không lệch > ±10% so với chu kỳ hiện tại
[ ] Đã implement fallback fixed-time khi AI fail/timeout
[ ] Đã implement kill switch UI/CLI per crossId
[ ] Đã implement audit log lưu request + response + X-Request-Id
[ ] Đã handle 7 error codes theo bảng §11.4
[ ] Đã test failover (kill ai-runtime → fallback)
[ ] Đồng ý quy trình điều tra sự cố

Ký xác nhận: __________________________
Ngày:        __________________________
```

### A.6 Lệnh upload sim bundle lên MinIO

```bash
SERVER=ai-service.internal.company.local
BUNDLE=grid4x4-v1.0.0.sim.zip

scp dist/$BUNDLE deploy@$SERVER:/tmp/

ssh deploy@$SERVER "docker run --rm --network ai_internal \
    -v /tmp:/data \
    --entrypoint /bin/sh \
    minio/mc:latest \
    -c 'mc alias set local http://minio:9000 minioadmin_prod <pwd> && \
        mc cp /data/$BUNDLE local/ai-models/sim/default/grid4x4/$BUNDLE'"
```

### A.7 Trigger scan thủ công (nếu auto-sync không pickup)

```bash
curl -X POST -H "X-Internal-API-Key: <key>" \
    https://ai-service.internal.company.local/ops/auto-sync/scan-now
```

---

## Phụ lục B — Vấn đề tồn đọng & đề xuất cải tiến

> Section này note lại các điểm chưa ổn trong luồng hiện tại, hoặc có thể cải tiến để tốt hơn trong tương lai. Không bắt buộc fix trước go-live, nhưng cần track.

### B.1 Vấn đề bảo mật

#### B.1.1 API key duy nhất cho mọi client
**Hiện trạng**: `X-Internal-API-Key` là 1 key chung dùng cho tất cả client gọi `/internal/sync/*` và `/ops/*`.

**Vấn đề**:
- Không phân biệt được caller nào nếu nhiều khách hàng cùng dùng.
- Key bị lộ → phải đổi cho tất cả.

**Đề xuất**: Chuyển sang JWT hoặc per-tenant API key, có audit theo từng key. Có thể implement middleware `src/core/auth.py` mở rộng.

#### B.1.2 Endpoint public `/api/algorithm/ai` không có auth
**Hiện trạng**: Bất kỳ ai gọi được endpoint inference cũng được phục vụ, miễn đến được mạng nội bộ.

**Vấn đề**: Nếu mạng nội bộ bị xâm nhập (ví dụ qua VPN bị lộ), attacker có thể spam request → DoS.

**Đề xuất**:
- Option 1: Yêu cầu API key cho cả endpoint public (đơn giản nhưng đổi contract).
- Option 2: mTLS — đội phần mềm điều khiển đèn cấp client cert, nginx verify.
- Option 3: Rate limit chặt hơn ở nginx + IP whitelist hard-coded.

#### B.1.3 Secrets management
**Hiện trạng**: Secrets lưu file plaintext trong thư mục `secrets/` chmod 600.

**Đề xuất**: Tích hợp HashiCorp Vault hoặc AWS Secrets Manager / Azure Key Vault. Service đọc secret qua API thay vì file.

### B.2 Vấn đề kiến trúc

#### B.2.1 Single point of failure
**Hiện trạng**: 1 server công ty chạy tất cả. Server down = toàn bộ AI hệ thống chết.

**Đề xuất**:
- Active-passive: 2 server, 1 standby qua keepalived.
- Active-active với load balancer phía trước (HAProxy/F5).
- Tách MySQL ra cluster riêng (MySQL Group Replication hoặc managed RDS).
- Tách MinIO ra cluster MinIO distributed mode (≥ 4 nodes).

#### B.2.2 Không có circuit breaker
**Hiện trạng**: Khi model inference chậm (CPU spike, ONNX issue), service vẫn cố trả response — tăng latency dần.

**Đề xuất**: Implement circuit breaker (file `src/runtime/circuit_breaker.py` chưa có) — khi p95 > 500ms sustained N giây, tạm thời trả lỗi để controller chuyển fixed-time, tránh kéo dài.

#### B.2.3 Auto-sync polling 600s quá dài
**Hiện trạng**: Auto-sync poller chạy 10 phút/lần. Bundle mới upload phải đợi tối đa 10 phút mới được pickup (nếu listener fail).

**Đề xuất**:
- Giảm xuống 60-120s cho production.
- Cải thiện listener (long-poll) reliability.

### B.3 Vấn đề observability

#### B.3.1 Chưa có distributed tracing
**Hiện trạng**: Chỉ có `X-Request-Id` correlation qua log.

**Đề xuất**: Tích hợp OpenTelemetry + Jaeger/Tempo. Trace từ controller → AI Service → ONNX inference → DB write.

#### B.3.2 Chưa có log structured cho audit
**Hiện trạng**: `inference_audit` table có data nhưng query SQL thủ công.

**Đề xuất**:
- Export audit log ra Elasticsearch / OpenSearch để query dễ hơn.
- Dashboard Grafana / Kibana cho audit.

#### B.3.3 Drift detection không tự động retrain
**Hiện trạng**: Drift detector chỉ alert, không trigger retrain pipeline.

**Đề xuất**: Tích hợp với MLflow + workflow engine (Airflow/Prefect) — khi drift > threshold, tự động gửi data về training, train model mới, push bundle.

### B.4 Vấn đề vận hành

#### B.4.1 Chưa có CI/CD cho deploy production
**Hiện trạng**: Jenkinsfile có build/test/push image nhưng deploy production phải manual SSH.

**Đề xuất**:
- Thêm stage "Deploy to staging/production" với approval gate.
- Dùng Ansible/Terraform để IaC.
- Hoặc chuyển sang Kubernetes + ArgoCD GitOps.

#### B.4.2 Chưa có canary deployment
**Hiện trạng**: Update bundle = thay toàn bộ. Nếu bundle mới có bug, ảnh hưởng tất cả area cùng lúc.

**Đề xuất**:
- Implement canary: 10% area dùng bundle mới, 90% dùng cũ, monitor 1h rồi promote.
- Cần thay đổi `bundle_resolver.py` để hỗ trợ weighted routing.

#### B.4.3 Chưa có chaos engineering
**Hiện trạng**: Test failover chỉ làm thủ công 1 lần ở Phase 2.

**Đề xuất**:
- Tích hợp chaos-mesh hoặc gremlin để inject failure định kỳ (mỗi tháng).
- Verify hệ thống tự recover.

### B.5 Vấn đề về API contract

#### B.5.1 Schema input không có versioning
**Hiện trạng**: `AIInput` schema cố định. Nếu cần thêm field, đội phần mềm điều khiển đèn phải đồng bộ deploy.

**Đề xuất**:
- Thêm `apiVersion` field trong request.
- Service support multiple versions trong cùng instance.

#### B.5.2 Không có OpenAPI spec
**Hiện trạng**: FastAPI tự gen Swagger ở `/docs` nhưng chưa export ra file static cho đội phần mềm điều khiển đèn generate client.

**Đề xuất**:
- Export `openapi.json` định kỳ vào git.
- Có CI check để chắc spec không break compat.

#### B.5.3 Direction encoding ambiguous
**Hiện trạng**: Code chỉ accept 4-direction encoding (1-4). 8-direction (0/2/4/6) chưa support đầy đủ ở cold-start fallback.

**Đề xuất**:
- Document rõ encoding nào support.
- Hoặc làm GPS-first triệt để — bỏ direction code khỏi runtime payload.

### B.6 Vấn đề về tài liệu

#### B.6.1 Tài liệu phân tán
**Hiện trạng**: Có nhiều file `docs/*.md` overlap nội dung (architecture, deployment, integration-real-controller, sim-to-real-pipeline).

**Đề xuất**: Refactor thành 1 cấu trúc rõ ràng:
```
docs/
├── 01-overview.md
├── 02-architecture.md
├── 03-deployment/
│   ├── server-deployment.md  (file này)
│   └── edge-deployment.md
├── 04-integration/
│   ├── api-contract.md
│   └── controller-integration.md
└── 05-operations/
    ├── monitoring.md
    ├── rollback.md
    └── troubleshooting.md
```

#### B.6.2 Thiếu runbook cho on-call
**Hiện trạng**: Có troubleshooting.md nhưng không có format runbook chuẩn cho on-call (alert → step-by-step action).

**Đề xuất**: Tạo `docs/runbooks/` với 1 file per alert type:
- `runbook-ai-service-down.md`
- `runbook-high-error-rate.md`
- `runbook-latency-spike.md`
- `runbook-drift-detected.md`

### B.7 Vấn đề về testing

#### B.7.1 Chưa có integration test end-to-end
**Hiện trạng**: Unit test có (tests/), nhưng chưa có test toàn pipeline: upload bundle → register area → call inference.

**Đề xuất**: Thêm `tests/integration/` với pytest fixture spawn full stack qua docker compose, chạy ở CI.

#### B.7.2 Chưa có load test định kỳ
**Hiện trạng**: Chỉ test load thủ công 24h ở Phase 2.

**Đề xuất**:
- Script locust trong `scripts/load_test.py`.
- Chạy weekly qua Jenkins.
- Baseline metric vào Grafana để track regression.

#### B.7.3 1 test fail đã biết
**Hiện trạng**: `test_guardrails.py::test_max_green_clip` fail, documented nhưng chưa fix.

**Đề xuất**: Fix trước khi go-live production.

### B.8 Tổng kết B — Mức độ ưu tiên

| Vấn đề | Mức độ | Tiến độ đề xuất |
|---|---|---|
| B.7.3 — Fix test guardrails | CRITICAL | Trước Phase 1 |
| B.1.1 — Per-tenant API key | HIGH | Sau Phase 2 |
| B.2.2 — Circuit breaker | HIGH | Trước Phase 3 |
| B.2.3 — Giảm polling interval | HIGH | Trước Phase 1 |
| B.4.2 — Canary deployment | HIGH | Trước Phase 5 |
| B.2.1 — HA / single point of failure | MEDIUM | Sau Phase 4 |
| B.3.1 — Distributed tracing | MEDIUM | Sau Phase 3 |
| B.5.2 — OpenAPI spec export | MEDIUM | Trước Phase 2 |
| B.1.3 — Secrets management (Vault) | LOW | Khi quy mô > 10 server |
| B.3.3 — Auto retrain pipeline | LOW | Phase 6+ |

---

## Tham chiếu

- [API contract chi tiết](ai-algorithm-service/api_docs/run_ai_algorithm.md)
- [Tài liệu integration gốc (edge model)](ai-algorithm-service/docs/integration-real-controller.md)
- [Architecture overview](ai-algorithm-service/docs/architecture.md)
- [Deployment model](ai-algorithm-service/docs/deployment.md)
- [Auto-sync mechanism](ai-algorithm-service/docs/auto-sync.md)
- [Troubleshooting](ai-algorithm-service/docs/troubleshooting.md)
- [README chính](README.md)

---

**Hết tài liệu.**

Mọi câu hỏi / phản hồi gửi về: AI Team — `<email>`
