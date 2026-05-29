# RL Algo for ITS — End-to-End Pipeline

Hướng dẫn step-by-step để chạy toàn bộ luồng: **huấn luyện xong → đóng gói model bundle → upload MinIO → service tự deploy → chạy inference**.

Repo này gồm 4 phần:

| Thư mục | Vai trò | Khi nào dùng |
|---|---|---|
| `Service-ai/` | Training (mô phỏng SUMO + MGMQ-PPO). Export ONNX. **Đóng gói Sim Bundle (.sim.zip)**. | Bước 1–3 |
| `ai-algorithm-service/` | Service inference 2-container (`ai-runtime` + `ai-ops`) + MinIO. Nhận Sim Bundle, tự compose Runtime Bundle, hot-reload. | Bước 4–7 |
| `bundle-tooling/` | Thư viện đóng gói Runtime Bundle (internal — service tự gọi, không phải bước thủ công). | — |
| `traffic_rl_features/` | Package shared (manifest schema). | — |

```
┌────────────────── Service-ai (training) ──────────────────┐
│                                                            │
│  preprocess_network → train → export ONNX → build_sim_bundle.py
│                                                       │    │
│                                                       ▼    │
│                                              dist/*.sim.zip│
└──────────────────────────────────────────────────────┬─────┘
                                                       │ mc cp
                                                       ▼
                                          ┌──────────────────┐
                                          │  MinIO (bucket   │
                                          │  ai-models,      │
                                          │  prefix sim/...) │
                                          └────────┬─────────┘
                                                   │ listener
                                                   ▼
┌─────────────── ai-algorithm-service ──────────────────────┐
│  ai-ops      │ compose Runtime Bundle (sim_network +      │
│              │ real_normalization → deployment_map)        │
│              │ activate → ghi active.json                  │
│              ▼                                              │
│  ai-runtime  │ poll active.json → load ONNX → inference   │
│              │ POST /api/algorithm/ai                      │
└────────────────────────────────────────────────────────────┘
```

---

## Yêu cầu

| Phần mềm | Phiên bản | Ghi chú |
|---|---|---|
| Python | 3.10–3.12 | Khuyến nghị 3.11 |
| Docker + Docker Compose | mới nhất | Cho service |
| SUMO | 1.24+ | Cho training |
| `uv` | mới nhất | Cài deps service (`pip install uv`) |
| Poetry | mới nhất | Cài deps training |

Set biến môi trường SUMO trước khi training:
```bash
export SUMO_HOME=/usr/share/sumo   # Linux
```

---

## Bước 1 — Tiền xử lý mạng lưới SUMO

Sinh `intersection_config.json` từ file `.net.xml` (chỉ làm một lần cho mỗi network):

```bash
cd Service-ai
poetry install
poetry shell

python scripts/preprocess_network.py --network grid4x4
```

Output: `Service-ai/network/grid4x4/intersection_config.json` — chứa lane aggregation, phase mapping, ma trận kết nối. File này sẽ được đóng gói vào Sim Bundle ở bước 3 với tên `sim_network.json`.

> Áp dụng tương tự cho các network khác: `cologne3`, `PhuQuoc`, `zurich`, …

---

## Bước 2 — Huấn luyện MGMQ-PPO

```bash
cd Service-ai

# Train cơ bản
python scripts/train_mgmq_ppo.py --network grid4x4 --iterations 500 --workers 4

# Hoặc với GPU + early stopping
python scripts/train_mgmq_ppo.py \
    --network grid4x4 \
    --iterations 1000 \
    --workers 8 \
    --gpu \
    --patience 50
```

Output:
```
Service-ai/results_mgmq/mgmq_ppo_grid4x4_<timestamp>/
├── checkpoints/
│   └── checkpoint_<iter>/
├── logs/
├── config.json
└── progress.csv
```

Theo dõi tiến độ qua TensorBoard:
```bash
tensorboard --logdir results_mgmq/
# → http://localhost:6006
```

Chi tiết các flag huấn luyện: [Service-ai/README.md](Service-ai/README.md).

---

## Bước 3 — Đóng gói Sim Bundle (.sim.zip)

Sim Bundle là artifact training bàn giao cho service. Gồm 4 file:

```text
sim_bundle_manifest.json   ← metadata (schema_version=1, network_id, version, ...)
policy.onnx                ← graph policy đã export từ checkpoint
policy_meta.json           ← obs_dim, action space, input names
sim_network.json           ← rename từ intersection_config.json
```

### 3.1 Export checkpoint sang ONNX

```bash
cd Service-ai

python scripts/export_checkpoint_onnx.py \
    --checkpoint results_mgmq/mgmq_ppo_grid4x4_<timestamp>/checkpoint_<iter> \
    --output tmp/onnx_eval/policy.onnx \
    --meta-output tmp/onnx_eval/policy_meta.json
```

### 3.2 Đóng gói Sim Bundle

```bash
cd Service-ai

python scripts/build_sim_bundle.py \
    --network grid4x4 \
    --network-id grid4x4 \
    --version v2026.05.22 \
    --policy-onnx tmp/onnx_eval/policy.onnx \
    --policy-meta tmp/onnx_eval/policy_meta.json \
    --output-zip dist/grid4x4-v2026.05.22.sim.zip
```

Cờ giải thích:
- `--network grid4x4` — script tự lấy `network/grid4x4/intersection_config.json` rồi rename thành `sim_network.json` trong ZIP. Nếu file ở chỗ khác, dùng `--sim-network <path>` thay thế.
- `--network-id` — định danh network bên service. Phải khớp với `networkId` trong real-network snapshot mà controller upload (bước 5).
- `--version` — phiên bản model. Đặt theo convention `v<YYYY.MM.DD>` hoặc semver.
- `--output-zip` — phải có đuôi `.sim.zip` (service filter theo suffix này).

Output:
```
[sim-bundle] OK id=sim-grid4x4-XXXXXXXX output=.../Service-ai/dist/grid4x4-v2026.05.22.sim.zip
[sim-bundle] Nội dung: sim_bundle_manifest.json, sim_network.json, policy.onnx, policy_meta.json
```

Verify ZIP:
```bash
python -c "import zipfile; z=zipfile.ZipFile('dist/grid4x4-v2026.05.22.sim.zip'); print('\n'.join(sorted(z.namelist())))"
```

> ⚠️ **Schema version**: Script đang ghi `schema_version=1`. Service chỉ chấp nhận giá trị nằm trong `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` (xem [ai-algorithm-service/src/ops/sim_bundle.py](ai-algorithm-service/src/ops/sim_bundle.py)). Khi bump, phải đồng bộ cả 2 repo.

---

## Bước 4 — Khởi động service stack (ai-runtime + ai-ops + MinIO)

```bash
cd ai-algorithm-service

# Sync deps lần đầu
uv lock
uv sync --extra dev

# Bật full stack: DB + MinIO + ai-runtime + ai-ops
docker compose --profile db --profile storage --profile app up -d
```

Containers chạy:

| Service | Cổng | Vai trò |
|---|---|---|
| `minio` | `9000` (API), `9001` (console) | Object storage chứa sim bundle |
| `minio-setup` | — | Tạo bucket `ai-models` lần đầu |
| `mysql` | `3306` | DB (hoặc SQLite local nếu skip profile `db`) |
| `ai-runtime` | `8001` | Inference endpoint `POST /api/algorithm/ai` |
| `ai-ops` | `8002` | Bundle lifecycle, auto-sync MinIO, sync API |

Verify:
```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
# MinIO console: http://localhost:9001  (minioadmin / minioadmin)
```

Auto-compose listener đã được bật mặc định trong `docker-compose.yml`:
```yaml
MINIO_AUTO_SYNC_ENABLED: "true"
SIM_BUNDLE_AUTO_COMPOSE_ENABLED: "true"
SIM_BUNDLE_PREFIX: "sim/default/"
SIM_BUNDLE_SUFFIX: ".sim.zip"
SIM_BUNDLE_AUTO_ACTIVATE: "true"
```

---

## Bước 5 — Đăng ký Real Network Snapshot

Service cần biết mạng lưới thực tế để compose `real_normalization.json` (chuẩn hoá obs sang space training). Controller gọi:

```
PUT /internal/sync/areas/{area_id}/real-network
Header: X-Internal-API-Key: <INTERNAL_API_KEY>
```

Cho demo, dùng script sẵn có để register snapshot mẫu Cologne3:

```bash
cd ai-algorithm-service

python scripts/register_demo_real_network_snapshot.py \
    --service-area-id 1 \
    --tenant-id default \
    --network-id cologne3 \
    --ops-url http://localhost:8002 \
    --api-key sondinh2k3
```

Verify snapshot:
```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
    http://localhost:8002/ops/areas/1/real-network
```

> 💡 **Race-condition handling**: Nếu upload sim bundle (Bước 6) TRƯỚC khi register snapshot, bundle sẽ chuyển sang status `pending_real_snapshot`. Khi snapshot tới, service tự retry compose. Hai luồng độc lập nhau.

---

## Bước 6 — Upload Sim Bundle lên MinIO

Sau khi đã có Sim Bundle (Bước 3) + Real Network Snapshot (Bước 5), upload ZIP lên MinIO. Listener của `ai-ops` sẽ tự pickup trong 1-2 giây.

### Path convention

```
s3://ai-models/sim/{tenant_id}/{network_id}/{network_id}-{version}.sim.zip
```

- **Prefix `sim/default/`** — phải khớp `SIM_BUNDLE_PREFIX` trong compose.
- **Suffix `.sim.zip`** — phải khớp `SIM_BUNDLE_SUFFIX`.

### Upload bằng `mc` (MinIO Client)

```bash
# Từ thư mục repo (RL_algo_for_ITS_Toan/)
docker run --rm --network ai-algorithm-service_default \
    -v "$PWD/Service-ai/dist:/data" \
    --entrypoint /bin/sh \
    minio/mc:latest \
    -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
        mc cp /data/grid4x4-v2026.05.22.sim.zip \
        local/ai-models/sim/default/grid4x4/grid4x4-v2026.05.22.sim.zip"
```

Hoặc qua MinIO console (`http://localhost:9001`):
1. Vào bucket `ai-models`
2. Tạo path `sim/default/grid4x4/` (nếu chưa có)
3. Upload `Service-ai/dist/grid4x4-v2026.05.22.sim.zip`

---

## Bước 7 — Verify auto-compose + activate

Sau ~1-2 giây từ lúc upload, `ai-ops` sẽ tự động:

1. **Pull** `.sim.zip` về.
2. **Validate** schema_version + đủ 4 file.
3. **Tìm** real_network_snapshot cho `(tenant, network_id)`.
4. **Compile** `real_normalization.json` từ snapshot.
5. **Compose** runtime bundle (sim_network + real_normalization + deployment_map + compatibility_report).
6. **Register + activate** bundle (ghi `active.json`).
7. `ai-runtime` poll `active.json` và hot-reload model (< 1s, không downtime).

### Trigger scan thủ công (nếu cần)

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
    http://localhost:8002/ops/auto-sync/scan-now
```

### Verify bundle đã active

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
    http://localhost:8002/ops/networks/grid4x4/active

curl http://localhost:8001/api/algorithm/ai/areas/1/readiness
# Expected: {"areaId": 1, "ready": true, "source": "bundle"}
```

### Check logs nếu lỗi

```bash
docker compose logs -f ai-ops | grep -E "sim-bundle|compose|active"
docker compose logs -f ai-runtime | grep -E "active|reload"
```

---

## Bước 8 — Chạy inference

`ai-runtime` expose endpoint chính:

```
POST http://localhost:8001/api/algorithm/ai
Content-Type: application/json
```

Service tự tra cứu bundle theo `areaId` trong request → load ONNX → trả signal plan.

### Test bằng payload mẫu

Repo có sẵn 2 payload demo:

```bash
cd ai-algorithm-service

# Payload Cologne3 (5 nút giao thật)
curl -X POST http://localhost:8001/api/algorithm/ai \
    -H "Content-Type: application/json" \
    -d @test_cologne3_payload.json | python -m json.tool

# Payload đơn giản
curl -X POST http://localhost:8001/api/algorithm/ai \
    -H "Content-Type: application/json" \
    -d @test_payload.json | python -m json.tool
```

### Response shape (rút gọn)

```json
{
  "status": 1,
  "numIntersections": 5,
  "areaIds": [1],
  "results": [
    {
      "crossId": 567005,
      "stagePlan": [
        {"stageId": 1, "duration": 28},
        {"stageId": 2, "duration": 35}
      ],
      "policyVersion": "v2026.05.22",
      "bundleId": "sim-grid4x4-XXXXXXXX",
      "guardrailTriggered": false
    }
  ]
}
```

Schema đầy đủ: [ai-algorithm-service/api_docs/run_ai_algorithm.md](ai-algorithm-service/api_docs/run_ai_algorithm.md).

### Postman collection

```
ai-algorithm-service/postman/
```

Import collection + environment vào Postman để test các endpoint khác (sync, ops, readiness).

---

## Bước 9 — Rollback / Update model

Để deploy phiên bản mới, lặp lại Bước 3 + 6 với `--version` khác:

```bash
# Service-ai
python scripts/build_sim_bundle.py \
    --network grid4x4 --network-id grid4x4 --version v2026.05.23 \
    --policy-onnx tmp/onnx_eval/policy.onnx \
    --policy-meta tmp/onnx_eval/policy_meta.json \
    --output-zip dist/grid4x4-v2026.05.23.sim.zip

# Upload
mc cp dist/grid4x4-v2026.05.23.sim.zip \
    local/ai-models/sim/default/grid4x4/grid4x4-v2026.05.23.sim.zip
```

Listener tự pickup version mới, build runtime bundle mới, activate. `active.json` trỏ sang bundle mới — `ai-runtime` hot-reload trong < 1s.

Để rollback về bundle cũ:

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
    -H "Content-Type: application/json" \
    -d '{"bundleId": "sim-grid4x4-XXXXXXXX"}' \
    http://localhost:8002/ops/networks/grid4x4/activate
```

---

## Troubleshooting nhanh

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `Sim bundle schema_version=X khong duoc ho tro` | Schema version trong `Service-ai/scripts/build_sim_bundle.py` không khớp service | Đồng bộ với `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` trong [ai-algorithm-service/src/ops/sim_bundle.py](ai-algorithm-service/src/ops/sim_bundle.py) |
| Bundle ở `pending_real_snapshot` | Chưa register real_network_snapshot | Làm Bước 5, service tự retry |
| `Sim bundle ZIP thiếu file bắt buộc` | ZIP build sai contract | Re-run `build_sim_bundle.py`, verify 4 file qua `zipfile.namelist()` |
| Listener không pickup bundle mới | Path không khớp prefix/suffix | Verify path đúng `sim/default/<network>/...sim.zip` |
| `readiness: false` | Bundle chưa active hoặc snapshot thiếu | Check `ops/networks/<id>/active`, log `ai-ops` |
| `ai-runtime` không reload | `active.json` không thay đổi mtime | Force trigger qua `POST /ops/auto-sync/scan-now` |

Chi tiết: [ai-algorithm-service/docs/troubleshooting.md](ai-algorithm-service/docs/troubleshooting.md).

---

## Tài liệu chuyên sâu

| Tài liệu | Nội dung |
|---|---|
| [ai-algorithm-service/docs/PIPELINE.md](ai-algorithm-service/docs/PIPELINE.md) | Luồng end-to-end chi tiết tiếng Việt |
| [ai-algorithm-service/docs/end-to-end-test.md](ai-algorithm-service/docs/end-to-end-test.md) | Test pipeline sim → runtime → inference |
| [ai-algorithm-service/docs/auto-sync.md](ai-algorithm-service/docs/auto-sync.md) | Cơ chế auto-deploy MinIO listener |
| [ai-algorithm-service/docs/architecture.md](ai-algorithm-service/docs/architecture.md) | Architecture mapping spec → code |
| [ai-algorithm-service/docs/deployment.md](ai-algorithm-service/docs/deployment.md) | Multi-tenant edge deployment |
| [ai-algorithm-service/docs/configuration.md](ai-algorithm-service/docs/configuration.md) | ENV variables reference |
| [Service-ai/README.md](Service-ai/README.md) | Training MGMQ-PPO chi tiết |
| [Service-ai/docs/MGMQ_Algorithm_Documentation.md](Service-ai/docs/MGMQ_Algorithm_Documentation.md) | Kiến trúc thuật toán |
| [Service-ai/docs/Training_Testing_Pipeline.md](Service-ai/docs/Training_Testing_Pipeline.md) | Tuning hyperparameters |
