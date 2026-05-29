# Pipeline Sim -> Real

> 📌 **Tài liệu chi tiết end-to-end tiếng Việt**: [PIPELINE.md](PIPELINE.md). File này là bản tóm tắt contract sau refactor.

Tài liệu này mô tả contract sau refactor. Guide chạy demo chi tiết nằm ở [end-to-end-test.md](end-to-end-test.md).

```text
[Sim Training]
  policy.onnx
  policy_meta.json
  sim_network.json
        |
        v
  Sim Bundle (*.sim.zip) -> MinIO
        |
        v
[AI Ops]
  pull Sim Bundle
  load real_network_snapshot tu DB noi bo service
  compile real_normalization.json
  generate deployment_map.json noi bo
  validate compatibility
  build + activate Runtime Bundle
        |
        v
[AI Runtime]
  controller payload -> inference
```

---

## 1. Sim Bundle

Training team chỉ cần upload một ZIP:

```text
cologne3.sim.zip
├── policy.onnx
├── policy_meta.json
├── sim_network.json
└── sim_bundle_manifest.json
```

Manifest:

```json
{
  "schema_version": 1,
  "sim_bundle_id": "sim-cologne3-xxxxxxxx",
  "tenant_id": "default",
  "network_id": "cologne3",
  "version": "v2026.05.15",
  "sim_network_path": "sim_network.json",
  "policy_onnx_path": "policy.onnx",
  "policy_meta_path": "policy_meta.json"
}
```

Build local (chạy trong repo training `Service-ai`):

```powershell
cd Service-ai
.\.venv\Scripts\python.exe -X utf8 scripts\build_sim_bundle.py `
  --tenant-id default `
  --network-id cologne3 `
  --version v2026.05.15 `
  --sim-network network\cologne3\intersection_config.json `
  --policy-onnx tmp\onnx_eval\policy.onnx `
  --policy-meta tmp\onnx_eval\policy_meta.json `
  --output-zip dist\cologne3.sim.zip
```

---

## 2. Real Network Snapshot

Service điều khiển/central backend phải push vùng điều khiển sang AI service:

```http
PUT /internal/sync/areas/{area_id}/real-network
```

Payload chính:

| Field | Ý nghĩa |
|---|---|
| `tenantId` | tenant sở hữu network |
| `networkId` | ID logic chung với Sim Bundle |
| `area` | thông tin vùng, tham chiếu `v_area` |
| `areaCrosses` | các cross thuộc vùng, tham chiếu `v_area_cross` |
| `crosses` | thông tin nút giao, tham chiếu `v_cross` |
| `roads` | đường/làn/hướng/capacity, tham chiếu `v_road` |
| `cycles` | chu kỳ đèn, tham chiếu `v_cycle` |
| `stages` | stage/phase thật, tham chiếu `v_stage` |
| `simToReal` | mapping explicit `sim_tls_id -> real_cross_id` |

AI service lưu payload vào bảng `real_network_snapshot`. Khi compose bundle, [src/ops/real_normalization.py](../src/ops/real_normalization.py) ưu tiên đọc bảng này. Các view legacy `v_area_cross`, `v_cross`, `v_road`, `v_cycle`, `v_stage` chỉ còn là fallback/debug.

### 2.1 Direction inference

`real_normalization` ánh xạ mỗi road của một cross sang hướng chuẩn `{N, E, S, W}` theo thứ tự ưu tiên:

1. **GPS** — nếu cross có `location` (hoặc `center_coordinate`) và road có `coordinates` (polyline kiểu `v_road_coordinate`), service tự tính vector INTO junction và bucket theo cùng cung 90° như GPI sim ([Service-ai standardizer.py:149](../../Service-ai/src/preprocessing/standardizer.py)). Khi 2 road cùng bucket, road có angle gần ideal (N=270°, E=180°, S=90°, W=0°) thắng.
2. **Legacy code** — `from_cross_direction` / `to_cross_direction`. Service auto-detect 4-direction (1..4) vs 8-direction (0/2/4/6) per snapshot.
3. **Không có gì → drop** — không round-robin fallback. Composer raise `DIRECTION_MISSING_IN_REAL` để fail loud thay vì silent-misroute observation channel.

Chi tiết: [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback).

---

## 3. Auto Compose

Khi `ai-ops` nhận Sim Bundle:

1. Validate Sim Bundle (gồm `schema_version` ∈ tập hỗ trợ).
2. Resolve `area_id` từ `area_registry` bằng `(tenant_id, network_id)`.
3. **Kiểm tra `real_network_snapshot` đã có chưa**:
   - Nếu chưa → set `status='pending_real_snapshot'` và dừng. Khi controller upload snapshot, service tự retry compose.
   - Nếu rồi → tiếp tục.
4. Compile `real_normalization.json` từ `real_network_snapshot`.
5. Generate `deployment_map.json` từ `sim_network.json + real_normalization.json`.
6. Validate compatibility.
7. Build Runtime Bundle.
8. Nhúng `sim_network.json`, `real_normalization.json`, `compatibility_report.json`.
9. Register + activate Runtime Bundle cho `network_id`.

`deployment_map.json` là artifact nội bộ, không phải file operator chỉnh tay.

### 3.1 Eager compile real_normalization

Khi controller gọi `PUT /internal/sync/areas/{id}/real-network`, service:

1. Lưu snapshot vào `real_network_snapshot` table.
2. **Eager** compile `real_normalization.json` ngay → ghi vào `{model_dir}/real_normalization/area_{id}/`.
3. Gọi `retry_pending_sim_bundles(tenant_id, network_id)` để kick các sim bundle đang chờ.

Controller có thể verify chuẩn hoá đã sẵn sàng:

```http
GET /internal/sync/areas/{area_id}/real-normalization
```

Hoặc recompile khi sửa data thô:

```http
POST /internal/sync/areas/{area_id}/real-normalization/recompile
```

---

## 4. Compatibility Gates

| Gate | Fail khi |
|---|---|
| Cross mapping | Sim cross không có real cross tương ứng |
| Direction mapping | Sim có hướng nhưng real snapshot thiếu road theo hướng đó |
| Phase/stage count | Số phase sim khác số stage real trong primary cycle |
| Bundle validation | Runtime bundle thiếu file hoặc checksum sai |
| Policy contract | `policy_meta.json` không khớp input/output ONNX |

Production nên cung cấp `simToReal` explicit. Nếu không có mapping explicit, composer chỉ auto-map theo thứ tự khi số cross bằng nhau và ghi warning `AUTO_CROSS_MAPPING_BY_ORDER`.

---

## 5. Runtime Bundle

Runtime Bundle cuối cùng:

```text
runtime-bundle.zip
├── policy.onnx
├── policy_meta.json
├── network.json
├── intersections/
│   ├── cross_<real_id>.json
│   └── ...
├── feature_formula.json
├── deployment_map.json
├── sim_network.json
├── real_normalization.json
├── compatibility_report.json
└── model_manifest.json
```

Runtime inference chỉ dùng real IDs từ controller payload. `sim_network.json`, `real_normalization.json`, `compatibility_report.json`, `deployment_map.json` được giữ lại để audit/debug.

---

## 6. Test Nhanh

```powershell
env APP_ENV=test DEBUG=false UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy `
  uv run pytest tests/test_sim_bundle_pipeline.py tests/test_runtime_v2.py tests/test_extractor_v2.py -q
```

Expected: tất cả pass (số test cụ thể thay đổi theo phiên bản — verify bằng `pytest --collect-only`).
