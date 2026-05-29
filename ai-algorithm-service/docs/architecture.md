# Architecture — AI Algorithm Service

> Tài liệu này mô tả kiến trúc nội bộ của service và mapping với spec gốc [kientrucRLOps.pdf](../kientrucRLOps.pdf) (4 lớp). Service implement đầy đủ Lớp 2 và phần lớn Lớp 4.
>
> 👉 Nếu bạn mới onboard, đọc [PIPELINE.md](PIPELINE.md) trước — file đó mô tả luồng end-to-end tiếng Việt chi tiết.

## 1. 4 lớp theo spec PDF

| Lớp | Tên | Vị trí | Service implement |
|-----|-----|--------|-------------------|
| 1 | Core Controller (Edge) | Phần mềm khách hàng | ❌ Out-of-scope |
| **2** | **AI Microservice** | **Edge Server** | ✅ **Toàn bộ** |
| 3 | Cloud (Data + CI/CD + Registry) | Vendor cloud (repo `Service-ai` build sim bundle) | ⚠️ Runtime composer + MLflow hooks |
| **4** | **Observability** | Cross-layer | ✅ **Prometheus + Grafana + Loki + Drift** |

## 2. Lớp 2 — AI Microservice (chi tiết)

Lớp 2 chia 2 container Docker chia sẻ Local Model Storage:

```
┌────────────── EDGE SERVER ──────────────┐
│                                          │
│  ┌──────────┐         ┌──────────────┐  │
│  │ ai-ops   │         │  ai-runtime  │  │
│  │ (8002)   │         │   (8001)     │  │
│  │          │         │              │  │
│  │ - Pull   │         │ - Preflight  │  │
│  │ - Validate│         │ - Inference  │  │
│  │ - Activate│         │ - Guardrails │  │
│  │ - Rollback│         │ - Drift      │  │
│  └────┬─────┘         └──────┬───────┘  │
│       │                      │           │
│       └──────┬───────────────┘           │
│              ▼                           │
│   ┌──────────────────────┐               │
│   │ Local Model Storage  │               │
│   │ /app/models/         │               │
│   │  ├─ networks/        │               │
│   │  │  └─ area_x/       │               │
│   │  │     ├─ active.json│               │
│   │  │     └─ bundles/   │               │
│   │  │        └─ <bid>/  │               │
│   │  └─ area_x/ (legacy) │               │
│   └──────────────────────┘               │
│                                          │
└──────────────────────────────────────────┘
```

### 2.1 Container ai-ops (port 8002)

Quản lý vòng đời Model Bundle. Spec mục IV.1.

**Trách nhiệm:**
- Pull bundle từ Artifact Store (MinIO/S3)
- Validate (file required, manifest schema, topology hash recompute, file checksums, config-level)
- Activate / Rollback
- Auto-sync (Phase 3 mới): listen MinIO bucket notification → pull tự động

**Code chính:**
| Spec mục | Code | File |
|----------|------|------|
| IV.1.1 Pull Bundle | `pull_and_register_bundle()` | [src/ops/lifecycle.py](../src/ops/lifecycle.py#L48) |
| IV.1.1 Validate | `validate_bundle_dir()` | [src/bundles/extractor.py](../src/bundles/extractor.py#L76) |
| IV.1.1 Activate | `activate_bundle()` | [src/ops/lifecycle.py](../src/ops/lifecycle.py#L223) |
| IV.1.1 Rollback | `rollback_bundle()` | [src/ops/lifecycle.py](../src/ops/lifecycle.py#L273) |
| IV.1.1 Quản lý Active | `read_active_pointer` / `write_active_pointer` | [src/bundles/active.py](../src/bundles/active.py) |
| Auto-sync (extension) | listener + poller | [src/ops/auto_sync.py](../src/ops/auto_sync.py) |

### 2.2 Container ai-runtime (port 8001)

Inference service. Spec mục IV.2.

**Pipeline 4 bước:**

```
Request từ Core Controller (raw observation per cross)
            │
            ▼
┌──────────────────────────────┐
│ 1. Topology Normalizer       │  → 12 lanes × 4 features (48-dim)
│    State Builder             │  + 8-dim green-time ratios (= 56-dim)
│    + observation_mask        │  + z-score
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 2. ONNX Inference Engine     │  → 8 standard phases (NEMA)
│    policy.onnx (CPU)         │  → 24 logits (8 phase × 3 actions)
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 3. Phase Normalizer          │  → map 8 phases về stages thực tế
│    (Action Mapper)           │  → mask -1 = "giữ nguyên"
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ 4. Guardrails (Safety Layer) │  → min/max green clip
│                              │  → anti-starvation
│                              │  → traffic rule (cycle length)
└──────────────┬───────────────┘
               ▼
Response: green-time per stage
```

**Code chính:**
| Spec mục | Code | File |
|----------|------|------|
| IV.2.1 Preflight Strict Mode | `run_preflight()` | [src/runtime/preflight.py](../src/runtime/preflight.py#L40) |
| IV.2.2 #1 Topology Normalizer | `build_lane_features` + 12-lane state | [src/preprocessing/topology_normalizer.py](../src/preprocessing/topology_normalizer.py) |
| IV.2.2 #1 Direction inference (compile-time) | `_classify_road_at_cross`, `_detect_legacy_direction_encoding` | [src/ops/real_normalization.py](../src/ops/real_normalization.py) |
| IV.2.2 #2 ONNX Inference | `policy.session.run()` | [src/services/ai_service.py](../src/services/ai_service.py#L234) |
| IV.2.2 #3 Phase Normalizer | `map_stage_actions` | [src/preprocessing/phase_normalizer.py](../src/preprocessing/phase_normalizer.py) |
| IV.2.2 #4 Guardrails | `apply_guardrails()` | [src/runtime/guardrails.py](../src/runtime/guardrails.py#L58) |
| IV.2 Pipeline orchestration | `AIService.run` + `_run_area` | [src/services/ai_service.py](../src/services/ai_service.py) |
| IV.3 Local Model Storage layout | network/bundles/active.json | [src/bundles/storage.py](../src/bundles/storage.py) |

> **Topology direction**: cột `direction_map` mỗi cross trong `network.json` được sinh ở compile time bằng GPI (sao chép từ Service-ai standardizer). Khi có GPS, service tự tính vector INTO junction từ polyline; khi không có, auto-detect legacy encoding 4-dir hoặc 8-dir. Chi tiết [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback). Lý do tách compile-time: runtime nhận `direction_map` đã chốt sẵn cùng bundle nên không phụ thuộc encoding DB của customer.

### 2.3 Bundle layout

Spec mục V.2.1: bundle ZIP gồm 5 file. Code khớp:

| File | Vai trò | Validate |
|------|---------|----------|
| `policy.onnx` | ONNX model weights | Required, checksum |
| `policy_meta.json` | hyperparameters, obs_stats, input/output names | Required, parsed by model_manager |
| `network.json` | topology graph (intersections, neighbors, directions) | Required, **topology_hash recompute** |
| `intersections/cross_<id>.json` | per-cross config (observation_mask, phase_mapping) | Optional, validate `mask==12`, `phase ∈ [-1,7]`, `≥1 phase hợp lệ` |
| `model_manifest.json` | metadata (bundle_id, tenant_id, network_id, version, hashes, file_checksums) | Required, schema validate |

### 2.4 Local Model Storage layout

```
/app/models/
├── networks/                          # bundle layout (Phase 1+ spec)
│   └── <network_id>/
│       ├── active.json                # active pointer (atomic write)
│       ├── archive/
│       │   └── <bundle_id>.zip        # ZIP archive sau pull
│       └── bundles/
│           ├── <bundle_id_v1>/        # extracted
│           │   ├── policy.onnx
│           │   ├── policy_meta.json
│           │   ├── network.json
│           │   ├── intersections/
│           │   └── model_manifest.json
│           └── <bundle_id_v2>/
│
└── area_<id>/                         # legacy layout (backward compat)
    ├── policy.onnx
    ├── policy_meta.json
    ├── network.json
    └── intersections/
```

**Active pointer (`active.json`):** trỏ đến bundle nào đang active cho 1 network. Write atomic qua `tempfile.mkstemp + os.replace`. ai-runtime poll mỗi 2s (TTL cache) để pickup bundle mới.

## 3. Lớp 3 — Cloud (slice đã làm)

Service không host Cloud. Pipeline mới phân vai rõ: repo training `Service-ai` build **Sim Bundle** (`scripts/build_sim_bundle.py`) và upload MinIO; ai-ops compose Runtime Bundle bằng bundle-tooling rồi activate. Jenkinsfile của repo service chỉ build/push Docker image — KHÔNG còn build sim bundle hay runtime bundle.

| Spec mục | Code | File |
|----------|------|------|
| V.2 CI/CD Packager (bundle-tooling) | `build_v2_bundle_zip()` | `../bundle-tooling/bundle_tooling/packager.py` |
| V.2 CLI script (internal) | `build-bundle v2` | `../bundle-tooling/bundle_tooling/cli.py` |
| Sim Bundle builder | `build_sim_bundle.py` (repo training Service-ai) | `../Service-ai/scripts/build_sim_bundle.py` |
| Runtime composer (ai-ops side) | `compose_runtime_bundle_from_sim_zip()` | [src/ops/composer.py](../src/ops/composer.py) |
| V.3 Artifact Store | MinIO (any S3) | [docker-compose.yml](../docker-compose.yml) profile `storage` |
| V.4 Model Registry | MLflow integration | [src/observability/mlflow_helper.py](../src/observability/mlflow_helper.py) |
| V.4 Lineage tracking | `model_bundle.training_run_id/dataset_id/pipeline_commit` | [src/db/models.py](../src/db/models.py) |
| V.5 Data Pipeline | ❌ Deferred | — |
| V.5 Retraining | ❌ Deferred | — |
| CI/CD Jenkins | Jenkinsfile build image (sim bundle do training repo build) | [Jenkinsfile](../Jenkinsfile) |

**Gap còn lại của Lớp 3:** Data Pipeline + Retraining (Offline RL PPO/GNN). Phần này chưa cần cho MVP — sẽ làm sau khi có dữ liệu thật từ pilot.

## 4. Lớp 4 — Observability

Implementing per spec mục VI.

| Spec mục | Code | File / Endpoint |
|----------|------|-----------------|
| VI.1 Real-time Monitoring | Grafana 6-panel dashboard | [observability/grafana/provisioning/dashboards/rlops-overview.json](../observability/grafana/provisioning/dashboards/rlops-overview.json) |
| VI.1 System Health | `/health`, `/ready` | FastAPI native |
| VI.1 Traffic KPIs | (chưa expose, deferred) | — |
| VI.1 Model Performance | `ai_inference_total{status}`, `ai_inference_latency_ms` | [src/observability/metrics.py](../src/observability/metrics.py) |
| VI.1 Alarms | `ai_guardrail_violations_total` | Phase 4 alert rules |
| VI.2 Drift Detection | PSI + KS, in-memory baseline + window | [src/observability/drift.py](../src/observability/drift.py) |
| VI.2 Drift wired vào AIService | `record_observation` mỗi inference | [src/services/ai_service.py](../src/services/ai_service.py#L226) |
| VI.2 Drift trigger retraining | ❌ Deferred (cần Lớp 3 retraining) | — |
| VI.3 Time Series DB | Prometheus | [observability/prometheus.yml](../observability/prometheus.yml) |
| VI.3 Log Storage | Loki + Promtail | [observability/promtail.yml](../observability/promtail.yml) |

## 5. Defense in Depth (spec mục VIII)

Spec yêu cầu 6 lớp safety. Service đã có 5/6 (Lớp 5 Heuristic Fallback thuộc Core Controller — Lớp 1).

| Lớp | Tên | Code |
|-----|-----|------|
| 1 | Bundle Validation (ai-ops) | `validate_bundle_dir()` — checksum, topology_hash, mask, phase_mapping |
| 2 | Preflight Check (ai-runtime) | `run_preflight()` — fail-fast khi swap bundle |
| 3 | Phase Normalizer (ai-runtime) | `map_stage_actions()` — chặn action không khớp cấu trúc |
| 4 | Guardrails (ai-runtime) | `apply_guardrails()` — min/max green, anti-starvation, exceeds_cycle |
| 5 | Heuristic Fallback (Core Controller) | ❌ Out-of-scope — đội Lớp 1 implement |
| 6 | Drift Detection (Observability) | `DriftDetector` + `drift_registry` |

## 6. Mapping mạng lưới và mô hình (spec mục IX)

Spec mục IX yêu cầu mỗi bundle gắn với 4 định danh: `tenant_id`, `network_id`, `topology_hash`, `bundle_id`. Service implement đầy đủ:

| Định danh | Trong Manifest | Trong DB | Verify khi |
|-----------|---------------|----------|-----------|
| `tenant_id` | ✅ | `model_bundle.tenant_id`, `area_registry.tenant_id` | Cross-validate khi pull |
| `network_id` | ✅ | `model_bundle.network_id` | Cross-validate khi pull (mục IX.2) |
| `topology_hash` | ✅ | `model_bundle.topology_hash` | **3 lần** — packaging, pull (validate), preflight |
| `bundle_id` | ✅ | `model_bundle.bundle_id` (PK) | — |

**Topology hash drift detection:** [src/bundles/topology_hash.py](../src/bundles/topology_hash.py) — SHA-256 của `network.json` đã canonicalize (filter structural keys + sort dict + sort list of dict by `id`). Recompute ở 3 nơi:
1. `validate_bundle_dir()` — khi extract ZIP
2. `_cross_validate()` (lifecycle) — khi pull
3. `run_preflight()` (runtime) — khi swap bundle hoặc startup

Nếu hash thực tế ≠ hash trong manifest → reject hoặc raise PreflightError.

## 7. Database schema

Các bảng chính (xem [src/db/models.py](../src/db/models.py)):

| Bảng | Vai trò |
|------|---------|
| `area_registry` | Area metadata + tenant_id + network_id |
| `area_artifact` | ⚠️ Legacy artifact (policy/meta/network paths) per area — endpoint deprecated |
| `area_cross_config` | ⚠️ Legacy per-cross config — pipeline mới không dùng |
| `real_network_snapshot` | Snapshot area/cross/road/cycle/stage do control service push vào AI service. **Nguồn chính cho composer.** |
| `sync_event` | Idempotency log cho sync API (sourceEventId) |
| `inference_audit` | Mỗi inference: state_norm, action, latency, bundle_id, guardrail_triggered |
| `model_bundle` | Bundle metadata. Status mới: `pending_real_snapshot` cho sim bundle chờ snapshot, `composed` sau retry thành công |
| `bundle_event` | Audit log mỗi thao tác lifecycle (pull/validate/activate/rollback/compose-deferred/compose-retry) |
| `drift_event` | Drift detection events (PSI/KS scores, severity) |
| `traffic_flow` | Traffic data từ MySQL init script (legacy) |

## 8. Key features (spec mục II.2)

| Feature | Status | Implementation |
|---------|--------|----------------|
| Safety First | ✅ | 6 lớp Defense in Depth (5 trong service) |
| Real-time < 100ms | ✅ | ONNX CPU inference đo p95 ~30-50ms |
| Continuous Learning | ⚠️ | Drift detection ✅, Retraining loop chưa làm |
| Fast Rollback < 1s | ✅ | DB update + atomic file write + HTTP notify, đo ~100-200ms |
| Single Source of Truth | ✅ | MinIO bucket — bundle versioned, không xóa |
| Model Lineage | ✅ | `model_bundle.training_run_id/dataset_id/pipeline_commit` + MLflow |

## 9. Tham khảo thêm

- [demo-quickstart.md](demo-quickstart.md) — chạy thử kiến trúc trên
- [auto-sync.md](auto-sync.md) — chi tiết cơ chế auto-deploy
- [api-reference.md](api-reference.md) — endpoints
- [../kientrucRLOps.pdf](../kientrucRLOps.pdf) — spec gốc 4 lớp
