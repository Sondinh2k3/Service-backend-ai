# Troubleshooting

> Common issues + cách debug. Chia theo phase deploy: setup, runtime, integration.

## 1. Setup / Docker

### 1.1 `docker compose build` báo "No services to build"

**Nguyên nhân:** ai-runtime và ai-ops đều ở profile `app`. Build mặc định không kích hoạt profile.

**Fix:**
```powershell
docker compose --profile app build
```

### 1.2 ai-runtime / ai-ops restart liên tục

**Triệu chứng:** `docker compose ps` thấy `Restarting (X)`.

**Diagnose:**
```powershell
docker compose logs --tail 50 ai-runtime
```

**Common causes:**

| Lỗi log | Nguyên nhân | Fix |
|---------|------------|-----|
| `Can't connect to MySQL server on 'mysql' (Name or service not known)` | MySQL container không chạy | `docker compose --profile db up -d mysql` rồi `docker compose restart ai-runtime ai-ops` |
| `Can't connect to MySQL server on 'mysql' (timed out)` | MySQL chưa healthy (đang init schema) | Đợi 30-60s, MySQL init lần đầu chậm |
| `Access denied for user 'root'@...` | Volume MySQL cũ với password khác | `docker compose down -v` rồi `up -d` lại (mất data) |
| `Cannot import 'src.main:app'` | Code lỗi syntax | `docker compose logs ai-runtime` xem traceback Python |

**Khuyến nghị:** luôn start với cả 3 profile:
```powershell
docker compose --profile db --profile storage --profile app up -d
```

### 1.3 `ModuleNotFoundError: No module named 'sqlalchemy'` khi `docker exec ... python ...`

**Nguyên nhân:** container có 2 Python:
- `/usr/local/bin/python` — system, KHÔNG có deps
- `/app/.venv/bin/python` — venv, có deps

`docker exec ... python` mặc định dùng system. Container CMD dùng `uv run uvicorn` để auto-activate venv.

**Fix:**
```powershell
docker exec ai_runtime uv run python -c "..."
# hoặc
docker exec ai_runtime /app/.venv/bin/python -c "..."
```

## 2. Pytest

### 2.1 `ModuleNotFoundError: No module named 'fastapi'` khi chạy pytest

**Nguyên nhân:** `pytest` ở `[project.optional-dependencies].dev` trong [pyproject.toml](../pyproject.toml). `uv run pytest` không thấy pytest trong main deps → tạo ephemeral env chỉ có pytest, bỏ qua main deps (fastapi, sqlalchemy, ...).

**Fix:**
```powershell
uv run --extra dev pytest tests/ -v
# hoặc sync 1 lần
uv sync --extra dev
uv run pytest tests/ -v   # giờ dùng project venv có đủ
```

### 2.2 `OperationalError: no such column: area_registry.tenant_id`

**Nguyên nhân:** DB SQLite cũ tạo trước khi schema có `tenant_id`. Migration chỉ chạy khi `init_db()` được gọi qua FastAPI lifespan.

**Fix:** Verify [tests/conftest.py](../tests/conftest.py) dùng context manager:
```python
@pytest.fixture
def client():
    with TestClient(app) as c:   # context manager → trigger lifespan
        yield c
```

Sau đó xóa DB cũ:
```powershell
Remove-Item ai_service.db -Force
uv run --extra dev pytest tests/ -v
```

## 3. Sync / Bundle deployment

### 3.1 `404 {"detail":"Not Found"}` khi gọi `/internal/sync/*`

**Nguyên nhân:** Endpoint `/internal/sync/*` chỉ mount trên container `ai-ops` (port 8002), KHÔNG phải `ai-runtime` (port 8001). Lý do: trong [src/main.py](../src/main.py) lifespan, router `internal_sync` chỉ attach khi `SERVICE_ROLE=ops` hoặc `all`.

**Fix:** Đổi base URL trong request từ `http://localhost:8001/...` → `http://localhost:8002/...`.

### 3.2 `405 {"detail":"Method Not Allowed"}` khi gọi sync API

**Nguyên nhân:** Đúng URL nhưng sai HTTP method.

**Reference:**
- B.1 upsert area: `PUT`
- B.2 upsert artifact: `PUT`
- B.3 activate artifact: `POST`
- B.4 sync cross config: `PUT`
- B.5 finalize: `POST`

### 3.3 `401 UNAUTHORIZED` mặc dù có header `X-Internal-API-Key`

**Diagnose:**
```powershell
docker exec ai_ops sh -c 'echo "INTERNAL_API_KEY=$INTERNAL_API_KEY"'
```

**Common causes:**
- Giá trị key trong request ≠ giá trị trong container env
- Container chưa restart sau khi sửa env
- Header name sai (mặc định `X-Internal-API-Key`, có thể đổi qua `INTERNAL_API_KEY_HEADER`)

**Fix:**
```powershell
docker compose up -d --force-recreate ai-ops
```

### 3.4 Bundle uploaded MinIO nhưng không tự deploy

**Diagnose:**
```powershell
$h = @{ "X-Internal-API-Key" = "<key>" }
Invoke-RestMethod -Uri http://localhost:8002/ops/auto-sync/status -Headers $h
```

**Checklist:**
1. `enabled: true` — đã bật `MINIO_AUTO_SYNC_ENABLED=true`?
2. `listener.alive: true` — listener đang chạy?
3. `MINIO_AUTO_SYNC_PREFIX` khớp với path upload thực tế? Local pipeline mới dùng `sim/default/`.
4. `MINIO_AUTO_SYNC_SUFFIX` khớp file upload? Local pipeline mới dùng `.sim.zip`.
5. Nếu upload Sim Bundle, `SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true`?

**Force scan:**
```powershell
Invoke-RestMethod -Method POST -Uri http://localhost:8002/ops/auto-sync/scan-now -Headers $h
```

**Check log:**
```powershell
docker compose logs ai-ops | Select-String "auto-sync"
```

### 3.6 Sim bundle ở status `pending_real_snapshot` (race-condition)

**Triệu chứng:** Bundle đã pull về nhưng chưa active. Log ai-ops:

```text
[ops] Sim bundle sim-cologne3-xxx cho network=cologne3 cho real snapshot.
      Se retry tu dong khi controller upload snapshot.
```

**Nguyên nhân:** Sim Bundle về MinIO **trước khi** controller gọi `PUT /internal/sync/areas/{id}/real-network` cho cùng `(tenantId, networkId)`. Service không thể compose vì thiếu real_normalization → giữ ở `pending_real_snapshot`.

**Đây là behavior đúng**, không phải bug. Cách xử lý:

```bash
# 1. List bundle pending
curl -H "X-Internal-API-Key: sondinh2k3" \
  "http://localhost:8002/ops/bundles?status=pending_real_snapshot"

# 2. Gửi real snapshot cho (tenantId, networkId) đó
python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 --tenant-id default --network-id cologne3 \
  --ops-url http://localhost:8002 --api-key sondinh2k3

# 3. Service TỰ ĐỘNG retry compose. Response sẽ có:
#    "retryPendingSimBundles": {"retried": 1, "succeeded": ["sim-..."], "failed": []}
```

Nếu retry vẫn fail, xem `rejected_reason` trong bundle metadata:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/bundles/<bundle_id>/events
```

### 3.7 `Sim bundle schema_version=X khong duoc ho tro`

**Nguyên nhân:** Sim bundle build với `schema_version` không nằm trong `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` ([src/ops/sim_bundle.py](../src/ops/sim_bundle.py)).

**Fix:**
- Rebuild sim bundle với `--version` đúng (script `build_sim_bundle.py` bên repo `Service-ai` hiện set schema_version=1).
- Nếu service đã upgrade schema, cập nhật `SUPPORTED_SIM_BUNDLE_SCHEMA_VERSIONS` để chấp nhận phiên bản cũ (backward compat).

### 3.8 Cảnh báo `[auto-sync] CANH BAO: ... overlap ...` khi khởi động

**Nguyên nhân:** `SIM_BUNDLE_PREFIX` và `ARTIFACT_BUNDLE_PREFIX` overlap. Composer upload runtime bundle lên MinIO sẽ trigger listener → vòng lặp.

**Fix:** Trong `docker-compose.yml` hoặc `.env`:

```env
SIM_BUNDLE_PREFIX=sim/
SIM_BUNDLE_SUFFIX=.sim.zip          # KHÔNG dùng '.zip' generic
ARTIFACT_BUNDLE_PREFIX=runtime/     # tách hoàn toàn
```

### 3.9 `DIRECTION_MISSING_IN_REAL` khi compose

**Triệu chứng:** `compatibility_report.json` (hoặc log ai-ops) báo:

```text
[composer] DIRECTION_MISSING_IN_REAL: sim cross 33202549 huong N nhung real_cross 567001
           khong co road tuong ung trong direction_map.
```

**Nguyên nhân:** `real_normalization` không xác định được hướng cho ít nhất 1 road, hoặc cross thiếu hẳn road ở 1 trong 4 hướng. Service đã bỏ round-robin fallback — fail loud thay vì silent-misroute (xem [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback)).

**Diagnose:**

```bash
# 1. Xem real_normalization.json đã compile
curl -H "X-Internal-API-Key: <key>" \
  http://localhost:8002/internal/sync/areas/<area_id>/real-normalization | jq '.content.crosses[].direction_map'

# 2. Xem log encoding nào được detect
docker compose logs ai-ops | grep "real_normalization"
# Mong đợi 1 trong 2:
#   [real_normalization] legacy direction encoding detected: 4-dir (1..4)
#   [real_normalization] legacy direction encoding detected: 8-dir (0/2/4/6)
```

**Common causes:**

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `direction_map` rỗng cho 1 cross | Cross thiếu cả GPS lẫn legacy code | Bổ sung `crosses[].location` hoặc `roads[].coordinates` trong payload `PUT /real-network` |
| Encoding detect ra 8-dir nhưng road có direction code 3 (= S trong 4-dir, SE trong 8-dir) | DB mixed encoding — 1 vài row dùng 8-dir, vài row dùng 4-dir | Chuẩn hoá toàn bộ DB về 1 encoding, hoặc cung cấp GPS để bypass legacy path |
| Cross thật 3-way nhưng sim 4-way | Topology mismatch — cross thật thiếu nhánh | Sim phải retrain với topology đúng, hoặc dùng cross sim 3-way tương đương |
| Direction = 5 / 7 (diagonal NE/NW) | DB lưu hướng diagonal | Bổ sung polyline GPS để service tự suy ra cardinal gần nhất qua GPI |

**Fix:**

```bash
# Sau khi sửa payload, recompile
curl -X POST -H "X-Internal-API-Key: <key>" \
  http://localhost:8002/internal/sync/areas/<area_id>/real-normalization/recompile

# Verify
curl -H "X-Internal-API-Key: <key>" \
  http://localhost:8002/internal/sync/areas/<area_id>/real-normalization | jq '.content.crosses[].direction_map'
# Mỗi direction_map phải có 4 key (hoặc 3 nếu cross 3-way) - giá trị tương ứng 0=N, 1=E, 2=S, 3=W
```

### 3.10 Inference `latency_ms` ổn nhưng output phase ratio "lệch" so với sim

**Triệu chứng:** Service chạy, không lỗi, nhưng greenTime AI đề xuất gần như cố định không phản ứng với traffic. Hoặc kết quả lệch hẳn so với expected.

**Nguyên nhân tiềm năng:** `direction_map` real đặt sai channel → observation vào policy bị hoán đổi hướng (vd traffic phía N được feed vào channel E).

**Diagnose:**

```bash
# So sánh direction_map real với observation_mask sim
curl -H "X-Internal-API-Key: <key>" \
  http://localhost:8002/internal/sync/areas/<area_id>/real-normalization \
  | jq '.content.crosses[] | {real_cross_id, observation_mask}'
```

`observation_mask` phải khớp với `observation_mask` trong `sim_network.json` của sim bundle (xem ZIP `cologne3.sim.zip` → `sim_network.json` → `intersections.<sim_tls_id>.observation_mask`).

Nếu khớp `observation_mask` nhưng vẫn lệch → kiểm tra `simToReal` mapping có đúng pair sim ↔ real không.

### 3.5 Bundle pull fail liên tục

**Diagnose:**
```powershell
docker exec ai_ops uv run python -c "from src.db.base import get_session; from src.db.models import BundleEvent; from sqlalchemy import select, desc; s = next(iter([get_session().__enter__()])); events = s.scalars(select(BundleEvent).order_by(desc(BundleEvent.created_at)).limit(10)).all(); [print(f'{e.event_type} {e.status}: {e.detail}') for e in events]"
```

**Common causes:**
- Bundle ZIP thiếu file required (manifest, policy.onnx)
- `topology_hash` trong manifest ≠ hash thực tế của `network.json` (file bị sửa sau khi build)
- File checksum mismatch (ZIP corrupt khi upload — re-upload)

## 4. Inference

### 4.1 `409 AREA_NOT_READY: missing=['policy.onnx', 'policy_meta.json', 'network.json']`

**Nguyên nhân phổ biến:** Runtime bundle chưa được compose/activate hoặc active.json trỏ sai network.

**Kiểm tra nhanh:**
```powershell
$h = @{ "X-Internal-API-Key" = "<key>" }
Invoke-RestMethod -Uri http://localhost:8002/ops/networks/<network_id>/active -Headers $h
Invoke-RestMethod -Uri http://localhost:8001/api/algorithm/ai/areas/<area_id>/readiness
```

**Checklist:**
1. `real_network_snapshot` đã được sync cho `area_id` (endpoint `PUT /internal/sync/areas/{area_id}/real-network`).
2. Sim Bundle đã upload đúng prefix/suffix (`sim/.../*.sim.zip`).
3. Auto-sync đang bật (`GET /ops/auto-sync/status`).
4. `active.json` tồn tại tại `/app/models/networks/<network_id>/active.json`.

**Fix:**
- Upload lại Sim Bundle đúng path.
- Trigger scan thủ công: `POST /ops/auto-sync/scan-now`.
- Xem log ai-ops: `docker compose logs ai-ops | Select-String "composer|auto-sync"`.

**Ghi chú:** Readiness hiện ưu tiên **bundle layout**, fallback legacy chỉ dành cho deployment cũ.

### 4.2 `422 Unprocessable Entity` cho `POST /api/algorithm/ai`

**Nguyên nhân:** Pydantic validation fail. Body schema sai.

**Diagnose:** Xem `details` array trong response — chỉ rõ field nào missing/wrong type.

**Common missing fields:**
- `crosses[].areaId` (required)
- `crosses[].cycle.{id, createdDate, modifiedDate, isActive, crossId, numberOfStages, oldId, cycleLength}` (đầy đủ)
- `crosses[].stages[].{id, stageCode, oldId, primary, weight, minGreenTime, maxGreenTime, yellow, redClear, duration}`
- `crosses[].roads[].{id, direction, numberOfLanes, flowRoad, saturationFlow, averageSpeed, occupancySpace}`

**Optional (khuyến nghị) để tính density từ flow/speed:** `totalVehicle`, `windowSeconds`, `averageSpeedUnit`, `queueLength`, `density`.

Tham khảo [test_cologne3_payload.json](../test_cologne3_payload.json) cho schema đầy đủ.

### 4.3 `400 MULTIPLE_AREAS_NOT_ALLOWED`

**Nguyên nhân:** Request có >1 `areaId`. Theo contract Lớp 1, mỗi request chỉ được 1 area.

**Fix:** Tách request thành nhiều request, mỗi request 1 area. Hoặc set `ENFORCE_SINGLE_AREA_PER_REQUEST=false` (không khuyến nghị production).

### 4.4 Latency > 200ms

**Diagnose:**
```powershell
docker compose logs ai-runtime | Select-String "latency_ms"
```

**Common causes:**
- **Cold start ONNX** — lần đầu load model mất 100-500ms. Lần 2 phải <100ms (model cached)
- **CPU throttle** — Docker container giới hạn CPU. Check `docker stats`
- **Large batch** — request có nhiều cross. Bình thường tỷ lệ thuận

**Fix:**
- Warm-up: gọi 5-10 request inference sau khi service start
- Tăng `intra_op_num_threads` trong `model_manager.py` (mặc định 1)
- Quantize ONNX → INT8 (Phase 4 task)

## 5. Drift detection

### 5.1 Drift không trigger event

**Diagnose:**
```powershell
$h = @{ "X-Internal-API-Key" = "<key>" }
Invoke-RestMethod -Uri http://localhost:8001/internal/runtime/drift -Headers $h
```

**Checklist:**
1. `baseline_features` có `obs_mean`? Nếu không → chưa đủ samples warmup. Cần ≥ `DRIFT_MIN_SAMPLES` (mặc định 200) request.
2. `window_sizes.obs_mean` > 0? Nếu yes nhưng không trigger → distribution không đổi nhiều.

### 5.2 Test trigger drift artificially

Tạo `test_payload_extreme.json` với observation lệch:
```json
{
  "crosses": [{
    "id": 1, "areaId": 1, "type": 1,
    "cycle": { ... },
    "stages": [ ... ],
    "roads": [
      {"id": 1, "direction": 1, "numberOfLanes": 3,
       "flowRoad": 5000, "saturationFlow": 5400,
       "averageSpeed": 1, "occupancySpace": 95.0,
       "insideArea": 1, "length": 142.34}
    ]
  }],
  "cycleTime": 90
}
```

Gửi 250 request với payload extreme → sau 200 baseline + check (mỗi 100), drift counter sẽ tăng.

```powershell
1..250 | ForEach-Object {
    Invoke-RestMethod -Method POST -Uri http://localhost:8001/api/algorithm/ai `
      -ContentType "application/json" -InFile test_payload_extreme.json | Out-Null
}
Invoke-RestMethod http://localhost:8001/metrics | Select-String "ai_drift_events_total"
```

## 6. Observability

### 6.1 Grafana dashboard không có data

**Diagnose:**
```powershell
# Prometheus có scrape ai-runtime?
Invoke-RestMethod http://localhost:9090/api/v1/targets | ConvertTo-Json -Depth 5 | Select-String "ai-runtime"
```

**Checklist:**
1. ai-runtime đang chạy → Prometheus phải scrape được
2. `/metrics` endpoint trả 200 với content `ai_inference_total ...`
3. Datasource Prometheus URL trong Grafana = `http://prometheus:9090` (Docker network internal)

### 6.2 ELK khong co log

**Diagnose:** Logstash doc file log Docker tu `/var/lib/docker/containers/*/*.log` theo config [observability/logstash/pipeline.conf](../observability/logstash/pipeline.conf).

```powershell
docker compose logs logstash | Select-String "ai-service-logs"
```

Neu Kibana khong thay data:

1. Verify Elasticsearch: `curl http://localhost:9200/_cluster/health`
2. Verify index: `curl http://localhost:9200/_cat/indices?v | Select-String "ai-service-logs"`
3. Tao Index Pattern trong Kibana: `ai-service-logs-*`

## 7. Multi-tenant / customer issues

### 7.1 Bundle của customer A bị deploy nhầm sang Edge customer B

**Nguyên nhân:** `MINIO_AUTO_SYNC_PREFIX` không set hoặc sai.

**Fix per-customer:**
```env
# Customer A
MINIO_AUTO_SYNC_PREFIX=tenant_kh_a/

# Customer B
MINIO_AUTO_SYNC_PREFIX=tenant_kh_b/
```

Vendor MinIO IAM policy nên enforce: customer chỉ list/read được prefix của mình → kể cả config sai cũng không leak data.

## 8. Performance / scaling

### 8.1 Memory leak (RAM tăng dần theo thời gian)

**Diagnose:** `docker stats` xem RAM ai-runtime.

**Common causes:**
- ONNX session cache không clear khi swap bundle. Verify [src/services/model_manager.py](../src/services/model_manager.py) `_cache` evict entries cũ.
- Drift detector window unbounded khi `DRIFT_WINDOW_SIZE=0`. Production set giá trị finite.

### 8.2 Concurrent requests fail

**Nguyên nhân:** ONNX session không thread-safe khi nhiều thread cùng `session.run()`.

**Fix hiện tại:** `intra_op_num_threads=1` trong session options + uvicorn worker model. Nếu cần throughput cao → multi-process uvicorn:
```dockerfile
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## 9. Tools để debug nhanh

| Cần | Lệnh |
|-----|------|
| Logs realtime của 1 service | `docker compose logs -f ai-runtime` |
| Logs filter pattern | `docker compose logs ai-ops \| Select-String "auto-sync"` |
| Vào shell container | `docker exec -it ai_runtime sh` |
| Run Python với deps đầy đủ | `docker exec ai_runtime uv run python -c "..."` |
| Inspect DB schema | `docker exec -it mysql_traffic mysql -uroot -p123456 statistic -e "SHOW TABLES; DESCRIBE area_registry;"` |
| Inspect bundle volume | `docker exec ai_runtime ls -la /app/models/networks/` |
| MinIO list bucket | qua Console http://localhost:9001 |
| Restart 1 service | `docker compose restart ai-runtime` |
| Force recreate (apply env mới) | `docker compose up -d --force-recreate ai-ops` |

## 10. Bước tiếp theo

- Nếu vẫn không fix được → log toàn bộ output, kèm `docker compose ps` + version, gửi cho team dev
- [end-to-end-test.md](end-to-end-test.md#01-quick-demo-10-phut-skip-race-conditionrollback) — chạy lại demo nhanh de khoanh vung loi
