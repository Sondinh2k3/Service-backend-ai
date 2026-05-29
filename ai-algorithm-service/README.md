# AI Algorithm Service

AI microservice cho hệ thống điều khiển đèn giao thông thông minh — Lớp 2 (Edge Server) trong kiến trúc RLOps. Inference ONNX policy từ Model Bundle, tự động deploy bundle mới từ MinIO/S3, observability đầy đủ qua Prometheus + Grafana + Loki.

## Tính năng chính

- **2-container split**: `ai-runtime` (inference) + `ai-ops` (bundle lifecycle), share Local Model Storage
- **Sim Bundle contract**: training team (repo `Service-ai`) đóng gói `policy.onnx + policy_meta.json + sim_network.json + sim_bundle_manifest.json` bằng `Service-ai/scripts/build_sim_bundle.py` và upload lên MinIO; service chỉ đọc/validate qua [src/ops/sim_bundle.py](src/ops/sim_bundle.py)
- **Real Network Snapshot contract**: controller/backend push `area + areaCrosses + crosses + roads + cycles + stages + simToReal` vào DB nội bộ của AI service
- **GPS-driven direction inference**: service tự suy hướng `N/E/S/W` từ `crosses[].location` + `roads[].coordinates` bằng thuật toán GPI giống Service-ai (cùng cung 90°, cùng tiebreaker "closest-to-ideal-angle"). Khi thiếu GPS, auto-detect legacy 4-dir (1..4) vs 8-dir (0/2/4/6) per snapshot. Chi tiết [docs/PIPELINE.md §4.6](docs/PIPELINE.md#46-direction-inference-gps-first-legacy-fallback).
- **Runtime Bundle format chuẩn**: `policy.onnx + policy_meta.json + network.json + intersections/cross_*.json + sim_network.json + real_normalization.json + compatibility_report.json + model_manifest.json`
- **Auto-deploy bundle**: ai-ops listen MinIO bucket notification, pull Sim Bundle, compile `real_normalization` từ snapshot nội bộ, validate compatibility, build Runtime Bundle và activate
- **Defense in Depth 6 lớp**: Bundle Validation → Preflight → Phase Normalizer → Guardrails → Heuristic Fallback → Drift Detection
- **Hot-reload < 1s**: ai-ops activate bundle mới → ai-runtime tự pickup qua active.json polling, không downtime
- **Observability**: Prometheus metrics, Grafana 6-panel dashboard, Loki logs, drift detection PSI/KS realtime
- **Idempotent sync API**: register real network snapshot / area / artifact / cross config qua `sourceEventId`

## Kiến trúc nhanh

```
┌─────────────────── VENDOR CLOUD ────────────────────┐
│                                                      │
│   Service-ai (training repo)                         │
│     scripts/build_sim_bundle.py → Push MinIO bucket  │
│                              │                       │
└──────────────────────────────┼───────────────────────┘
                               │ outbound HTTPS
                               │ (long-poll listener)
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         ┌────────┐       ┌────────┐       ┌────────┐
         │ EDGE 1 │       │ EDGE 2 │  ...  │ EDGE N │
         │        │       │        │       │        │
         │ ai-ops │       │ ai-ops │       │ ai-ops │
         │ ai-rt  │       │ ai-rt  │       │ ai-rt  │
         └───┬────┘       └───┬────┘       └────────┘
             │ POST /api/algorithm/ai
             ▼
      ┌────────────────┐
      │ Core Controller│  ← phần mềm khách hàng (Lớp 1)
      │ + Camera/Sensor│
      └────────────────┘
```

Chi tiết: [docs/architecture.md](docs/architecture.md) và [docs/deployment.md](docs/deployment.md).

## Quick start

**Yêu cầu:** Docker Desktop + Python 3.11+ + [uv](https://docs.astral.sh/uv/).

```powershell
cd ai-algorithm-service

# 1. Generate uv.lock + sync deps
uv lock
uv sync --extra dev

# 2. Khởi động full stack
docker compose --profile db --profile storage --profile app up -d

# 3. Verify
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:8002/health

# 4. Chạy demo end-to-end
# build Sim Bundle → upload MinIO → ai-ops compose Runtime Bundle → inference
# Xem docs/end-to-end-test.md
```

→ **[docs/end-to-end-test.md](docs/end-to-end-test.md)** là tài liệu chính để kiểm chứng pipeline Sim → Real đầy đủ. [docs/demo-quickstart.md](docs/demo-quickstart.md) là bản demo mở rộng có thêm rollback/observability.

## Cấu trúc thư mục

```
ai-algorithm-service/
├── src/
│   ├── api/              # FastAPI routers (public inference + internal sync)
│   ├── bundles/          # Model Bundle format (manifest, packager, extractor, topology hash)
│   ├── core/             # config, auth, exceptions, telemetry
│   ├── db/               # SQLAlchemy models + repositories + migrations
│   ├── observability/    # Prometheus metrics, drift detection, MLflow helper
│   ├── ops/              # ai-ops: bundle lifecycle, auto-sync MinIO
│   ├── preprocessing/    # Topology Normalizer, Phase Normalizer, Feature Normalizer
│   ├── runtime/          # ai-runtime: preflight, guardrails, bundle resolver
│   ├── schemas/          # Pydantic schemas (input/output, sync, common)
│   ├── services/         # ai_service, model_manager, audit, readiness, sync
│   └── main.py           # FastAPI app factory + lifespan (role-based router mounting via SERVICE_ROLE env)
├── tests/                # pytest suites cho runtime, extractor, guardrails, API
├── scripts/              # build_bundle, migrate_legacy, test helpers
├── observability/        # Prometheus + Grafana + Loki configs
├── postman/              # Postman collection + environment
├── api_docs/             # Inference API spec
├── docs/                 # Tài liệu kỹ thuật (đọc bắt đầu từ docs/README.md)
├── models/               # Sample model bundle
├── db/                   # MySQL init schema
├── docker-compose.yml    # 8 services, 4 profiles (db, storage, app, observability, mlflow)
├── Dockerfile            # Multi-stage Python 3.11 + uv
├── Jenkinsfile           # CI/CD pipeline service-side (checkout/lint/test/build image/push image)
├── pyproject.toml        # uv-managed deps
└── README.md             # bạn đang đọc
```

## Tech stack

| Khu vực | Công nghệ |
|---------|----------|
| Web framework | FastAPI 0.111 + uvicorn |
| Inference | ONNX Runtime 1.17+ |
| Validation | Pydantic 2.8 + pydantic-settings 2.4 |
| Database | SQLAlchemy 2.0 (MySQL production / SQLite dev) |
| Storage | MinIO (S3-compatible) |
| Observability | Prometheus + Grafana + Loki + Promtail |
| Model Registry | MLflow 2.13 (optional) |
| CI/CD | Jenkins (Jenkinsfile sẵn) |
| Package manager | uv |
| Test | pytest 8.2 |

## Tài liệu

| Tài liệu | Đối tượng |
|---------|----------|
| [docs/PIPELINE.md](docs/PIPELINE.md) | **🆕 ĐỌC ĐẦU TIÊN** — luồng end-to-end tiếng Việt chi tiết |
| [docs/end-to-end-test.md](docs/end-to-end-test.md) | Test pipeline Sim Bundle → Runtime Bundle → inference |
| [docs/demo-quickstart.md](docs/demo-quickstart.md) | Demo mở rộng — rollback, drift, Grafana |
| [docs/sim-to-real-pipeline.md](docs/sim-to-real-pipeline.md) | Tóm tắt refactor sim-to-real |
| [docs/architecture.md](docs/architecture.md) | Architect, lead dev — mapping spec PDF → code |
| [docs/deployment.md](docs/deployment.md) | DevOps — vendor cloud + customer edge |
| [docs/auto-sync.md](docs/auto-sync.md) | DevOps — cách auto-deploy bundle |
| [docs/api-reference.md](docs/api-reference.md) | Tích hợp Lớp 1 (Core Controller) |
| [docs/integration-real-controller.md](docs/integration-real-controller.md) | Kế hoạch tích hợp + chạy thử với phần mềm điều khiển đèn thật |
| [docs/configuration.md](docs/configuration.md) | Ops — env variables reference |
| [docs/testing.md](docs/testing.md) | Dev — chạy + thêm test |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Mọi người — debug khi gặp lỗi |
| [api_docs/run_ai_algorithm.md](api_docs/run_ai_algorithm.md) | Tích hợp — chi tiết `POST /api/algorithm/ai` |
| [postman/README.md](postman/README.md) | QA — Postman collection |

## Trạng thái dự án

**MVP / Beta-ready** — Lớp 2 hoàn chỉnh theo spec. Có thể demo và pilot ở 1 customer.

| Lớp (theo PDF) | Trạng thái |
|----------------|-----------|
| Lớp 1 — Core Controller | Out-of-scope (đội khác) |
| Lớp 2 — AI Microservice | ✅ 100% |
| Lớp 3 — Cloud (CI/CD + Registry) | ✅ 70% (CI/CD done, retraining pipeline deferred) |
| Lớp 4 — Observability | ✅ 90% (dashboards + drift done, alert rules deferred) |

## License

Proprietary — vui lòng liên hệ team trước khi reuse.
