# Demo Quickstart

> Bản demo rút gọn để chạy nhanh trong < 10 phút. Nếu cần verify đầy đủ pipeline (gồm race-condition + rollback), đọc [end-to-end-test.md](end-to-end-test.md) thay vào.

## Pipeline ngắn

```text
Start Docker stack
  → đăng ký real_network_snapshot cho areaId=1, networkId=cologne3
  → build dist/cologne3.sim.zip
  → upload MinIO sim/default/cologne3/cologne3.sim.zip
  → ai-ops compose runtime bundle + activate (~2s)
  → POST /api/algorithm/ai
```

---

## 1. Setup

```bash
cd ai-algorithm-service
uv lock
uv sync --extra dev
uv pip install -e ../traffic_rl_features -e ../bundle-tooling
```

Verify:

```bash
# Script build sim bundle nay nam ben repo training Service-ai (xem README ben do).
uv run pytest tests -q
```

---

## 2. Start Stack

```bash
docker compose --profile db --profile storage --profile app up -d --build
docker compose ps
```

| Service | URL |
|---|---|
| ai-runtime | http://localhost:8001 |
| ai-ops | http://localhost:8002 |
| MinIO Console | http://localhost:9001 |

Health:

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

---

## 3. Đăng ký Real Network Snapshot

Script đóng gói sẵn payload Cologne3:

```bash
python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3
```

Sau bước này, service đã:

- Lưu snapshot vào DB nội bộ.
- Eager compile `real_normalization.json`.
- Sẵn sàng nhận sim bundle.

Verify:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/internal/sync/areas/1/real-normalization | head -20
```

---

## 4. Build Sim Bundle

Sim bundle build bằng script bên repo training `Service-ai`:

```bash
cd ../Service-ai
mkdir -p dist
python scripts/build_sim_bundle.py \
  --tenant-id default \
  --network-id cologne3 \
  --version v2026.05.15 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3.sim.zip
cd ../ai-algorithm-service
```

Expected: `[sim-bundle] OK id=sim-cologne3-xxxxxxxx output=.../dist/cologne3.sim.zip`

---

## 5. Upload + Auto Deploy

`docker-compose.yml` đã bật composer mặc định:

```yaml
SIM_BUNDLE_AUTO_COMPOSE_ENABLED: true
SIM_BUNDLE_PREFIX: sim/default/
SIM_BUNDLE_SUFFIX: .sim.zip
SIM_BUNDLE_AUTO_ACTIVATE: true
```

Upload:

```bash
docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" \
  --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3.sim.zip local/ai-models/sim/default/cologne3/cologne3.sim.zip"
```

Đợi 1-2 giây cho listener pickup. Force scan nếu cần:

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/auto-sync/scan-now
```

Verify:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active

curl http://localhost:8001/api/algorithm/ai/areas/1/readiness
```

Readiness mong đợi: `{"areaId": 1, "ready": true, "source": "bundle"}`.

---

## 6. Run Inference

```bash
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -d @test_cologne3_payload.json | python -m json.tool
```

Expected:

| Field | Expected |
|---|---|
| `status` | `1` |
| `numIntersections` | `5` |
| `areaIds` | `[1]` |

Metrics:

```bash
curl http://localhost:8001/metrics | grep ai_inference
```

---

## 7. Rollback Demo

Build + upload version mới:

```bash
cd ../Service-ai
python scripts/build_sim_bundle.py \
  --tenant-id default --network-id cologne3 --version v2026.05.16 \
  --sim-network network/cologne3/intersection_config.json \
  --policy-onnx tmp/onnx_eval/policy.onnx \
  --policy-meta tmp/onnx_eval/policy_meta.json \
  --output-zip dist/cologne3-v2.sim.zip
cd ../ai-algorithm-service

docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/../Service-ai/dist:/data" --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3-v2.sim.zip local/ai-models/sim/default/cologne3/cologne3-v2.sim.zip"

curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/auto-sync/scan-now
```

Rollback về version trước:

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  -H "Content-Type: application/json" \
  -d '{"tenantId":"default"}' \
  http://localhost:8002/ops/networks/cologne3/rollback
```

---

## 8. Observability (optional)

```bash
docker compose --profile observability up -d
```

| Tool | URL | Login |
|---|---|---|
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | `admin` / `admin` |

Generate load:

```bash
for i in {1..100}; do
  curl -X POST http://localhost:8001/api/algorithm/ai \
    -H "Content-Type: application/json" \
    -d @test_cologne3_payload.json > /dev/null 2>&1
done
```

---

## 9. Cleanup

Stop containers (giữ data):

```bash
docker compose --profile db --profile storage --profile app --profile observability down
```

Stop + xoá volume (reset hoàn toàn):

```bash
docker compose --profile db --profile storage --profile app --profile observability down -v
```

---

## Bước tiếp theo

- [end-to-end-test.md](end-to-end-test.md) — test đầy đủ gồm race-condition, schema version, rollback
- [PIPELINE.md](PIPELINE.md) — hiểu pipeline về mặt kiến trúc
- [troubleshooting.md](troubleshooting.md) — khi gặp lỗi
