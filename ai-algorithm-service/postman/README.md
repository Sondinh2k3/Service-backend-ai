# Postman Collection

Collection và environment Postman sẵn dùng để test toàn bộ API của AI Algorithm Service.

## Files

| File | Mô tả |
|------|-------|
| `RLOps E2E.postman_collection.json` | Collection chính cho demo Cologne3, chia 4 folder |
| `RLOps Local.postman_environment.json` | Environment local đã điền sẵn port/API key/network |

## Cấu trúc collection

### Folder A — Health & Manifest
| Request | Endpoint |
|---------|----------|
| A.1 Health (Runtime) | `GET {{baseUrlRuntime}}/health` |
| A.2 Health (Ops) | `GET {{baseUrlOps}}/health` |
| A.3 Ready | `GET {{baseUrlRuntime}}/ready` |
| A.4 List active areas | `GET {{baseUrlRuntime}}/api/algorithm/ai/areas` |
| A.5 Auto-sync Status | `GET {{baseUrlOps}}/ops/auto-sync/status` |

### Folder B — Sync flow (legacy area registration)
> ⚠️ Endpoints này ở **port 8002 (ai-ops)**, không phải 8001.

| Request | Method | Endpoint |
|---------|--------|----------|
| B.1 Upsert area | PUT | `{{baseUrlOps}}/internal/sync/areas/{{areaId}}` |
| B.2 Upsert artifact | PUT | `{{baseUrlOps}}/internal/sync/areas/{{areaId}}/artifacts` |
| B.3 Verify readiness | GET | `{{baseUrlRuntime}}/api/algorithm/ai/areas/{{areaId}}/readiness` |

### Folder C — Bundle lifecycle (RLOps flow)
| Request | Method | Endpoint |
|---------|--------|----------|
| C.1 Scan MinIO now | POST | `{{baseUrlOps}}/ops/auto-sync/scan-now` |
| C.2 Read active pointer | GET | `{{baseUrlOps}}/ops/networks/{{networkId}}/active` |
| C.3 List bundles | GET | `{{baseUrlOps}}/ops/bundles?tenantId={{tenantId}}&networkId={{networkId}}` |
| C.4 Bundle audit events | GET | `{{baseUrlOps}}/ops/bundles/{{bundleId}}/events` |

> **Note:** Sau khi bật auto-sync ([../docs/auto-sync.md](../docs/auto-sync.md)), không cần gọi C.2/C.3 thủ công nữa — service tự pull khi có ZIP mới trên MinIO.

### Folder D — Inference
| Request | Method | Endpoint |
|---------|--------|----------|
| D.1 Inference Cologne3 5 cross | POST | `{{baseUrlRuntime}}/api/algorithm/ai` |
| D.2 Runtime drift snapshot | GET | `{{baseUrlRuntime}}/internal/runtime/drift` |
| D.3 Cache clear | POST | `{{baseUrlRuntime}}/api/algorithm/ai/cache/clear?area_id={{areaId}}` |

## Cách dùng

### 1. Import vào Postman

- File → Import → chọn 2 file `.json` ở folder này

### 2. Cấu hình environment

Mở environment `RLOps Local`, sửa các biến:

| Variable | Initial value | Mô tả |
|----------|--------------|------|
| `baseUrlRuntime` | `http://localhost:8001` | ai-runtime URL |
| `baseUrlOps` | `http://localhost:8002` | ai-ops URL |
| `apiKeyRuntime` | `sondinh2k3` | API key cho `/internal/*` của ai-runtime |
| `apiKeyOps` | `sondinh2k3` | API key cho `/ops/*` và `/internal/sync/*` của ai-ops |
| `tenantId` | `default` | Tenant ID |
| `networkId` | `cologne3` | Network ID demo |
| `areaId` | `1` | Area ID test |
| `crossId` | `567001` | Cross ID test trong payload Cologne3 |
| `bundleId` | _(auto-fill)_ | Tự lưu sau khi C.2 pull thành công |

> ⚠️ **Giá trị API key** phải khớp `INTERNAL_API_KEY` env trong container. Verify:
> ```powershell
> docker exec ai_ops sh -c 'echo $INTERNAL_API_KEY'
> ```

Đảm bảo chọn environment ở góc trên cùng bên phải Postman.

### 3. Workflow chạy

**Lần đầu / sau khi reset DB:**
1. Folder A → verify stack OK
2. Folder B (B.1 → B.2 → B.6) → register area `networkId=cologne3`, artifact legacy cho readiness
3. Build Sim Bundle chứa `sim_network.json`, upload `dist/cologne3.sim.zip` lên MinIO path `sim/default/cologne3/cologne3.sim.zip`, rồi Folder C để scan/verify auto-sync
4. Folder D → test inference bằng payload `test_cologne3_payload.json`

**Sau khi setup xong, hàng ngày:**
- Chỉ cần Folder D để test inference

### 4. Collection Runner

Click vào collection `RLOps E2E - Cologne3` → **Run** → chọn folder và requests → Run.

Postman chạy lần lượt, lưu biến (như `bundleId`, `artifactId`) tự động vào environment.

## Workflow chi tiết

Xem [../docs/end-to-end-test.md](../docs/end-to-end-test.md) để có hướng dẫn step-by-step end-to-end (gồm cả setup Docker, build bundle, upload MinIO).

## Troubleshooting Postman

| Lỗi | Hint |
|-----|------|
| `401 Unauthorized` | Kiểm tra biến `apiKeyOps`/`apiKeyRuntime` khớp `INTERNAL_API_KEY` trong container |
| `404 {"detail":"Not Found"}` | Sai port. `/internal/sync/*` → port 8002, `/api/algorithm/ai/*` → port 8001 |
| `405 Method Not Allowed` | Sai HTTP method. B.1/B.2 = PUT, C.1/D.3 = POST |
| `409 AREA_NOT_READY` | Chưa copy legacy model files hoặc area chưa map `networkId=cologne3`. Xem [../docs/end-to-end-test.md](../docs/end-to-end-test.md#7-inference-test) |
| Biến `{{bundleId}}` rỗng | C.2 chưa chạy thành công hoặc chưa có active bundle. Upload ZIP rồi chạy C.1/C.2 |

Common issues đầy đủ: [../docs/troubleshooting.md](../docs/troubleshooting.md).

## Cập nhật collection

Khi service thêm endpoint mới:

1. Test request mới trong Postman
2. Cập nhật collection trong Postman
3. Export Collection v2.1 → ghi đè `RLOps E2E.postman_collection.json`
4. Commit cùng PR thay đổi code
