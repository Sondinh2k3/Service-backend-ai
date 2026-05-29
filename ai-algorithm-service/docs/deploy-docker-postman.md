# Docker + Postman Demo

> Bản rút gọn chạy Docker và test bằng Postman. Luồng chi tiết hơn xem [end-to-end-test.md](end-to-end-test.md).

---

## 1. Start Stack

```bash
docker compose --profile db --profile storage --profile app up -d --build
docker compose ps
```

Health:

```bash
curl http://localhost:8001/health
curl http://localhost:8002/health
```

---

## 2. Đăng ký Real Network Snapshot

```bash
python scripts/register_demo_real_network_snapshot.py \
  --service-area-id 1 \
  --tenant-id default \
  --network-id cologne3 \
  --ops-url http://localhost:8002 \
  --api-key sondinh2k3
```

Response sẽ có thêm 2 field mới (sau khi update):

- `realNormalization`: kết quả eager compile `real_normalization.json`.
- `retryPendingSimBundles`: nếu sim bundle về trước, sẽ tự retry compose tại đây.

---

## 3. Build Sim Bundle

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

ZIP expected:

```text
policy.onnx
policy_meta.json
sim_network.json
sim_bundle_manifest.json   (schema_version=1)
```

---

## 4. Upload MinIO + Auto Deploy

```bash
docker run --rm --network ai-algorithm-service_default \
  -v "$PWD/dist:/data" \
  --entrypoint /bin/sh \
  minio/mc:latest \
  -c "mc alias set local http://minio:9000 minioadmin minioadmin && \
      mc cp /data/cologne3.sim.zip local/ai-models/sim/default/cologne3/cologne3.sim.zip"

curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/auto-sync/scan-now
```

Verify:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/cologne3/active

curl http://localhost:8001/api/algorithm/ai/areas/1/readiness
```

Readiness expected: `ready=true`, `source=bundle`.

---

## 5. Postman

Import:

- [../postman/RLOps E2E.postman_collection.json](../postman/RLOps%20E2E.postman_collection.json)
- [../postman/RLOps Local.postman_environment.json](../postman/RLOps%20Local.postman_environment.json)

Thứ tự chạy:

1. **Folder A** — health/status.
2. **Đăng ký snapshot** bằng PowerShell/script ở bước 2 (collection không có sẵn payload đầy đủ; user tự tạo request hoặc dùng script).
3. **Folder C** — scan MinIO + active pointer.
4. **Folder D** — inference Cologne3.

Payload chính: [../test_cologne3_payload.json](../test_cologne3_payload.json).

---

## 6. Endpoint mới đáng chú ý

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/internal/sync/areas/{id}/real-normalization` | Xem file chuẩn hoá đã compile cho area |
| `POST` | `/internal/sync/areas/{id}/real-normalization/recompile` | Recompile sau khi sửa data thô |
| `GET` | `/ops/bundles?status=pending_real_snapshot` | Xem sim bundle đang chờ snapshot |
| `POST` | `/ops/sim-bundles/pull` | Pull sim bundle thủ công từ URI (skip listener) |

---

## 7. Troubleshooting

| Lỗi | Cách kiểm tra |
|---|---|
| `AREA_NOT_READY` | `GET /api/algorithm/ai/areas/1/readiness` |
| `bundle status=pending_real_snapshot` | Gửi snapshot ở bước 2 — service tự retry |
| Bundle không deploy | `GET /ops/auto-sync/status`, `docker compose logs --tail 80 ai-ops` |
| Compatibility fail | Xem `compatibility_report.json` trong runtime bundle hoặc log ai-ops |
| Sai API key | Header `X-Internal-API-Key: sondinh2k3` |
| `schema_version not supported` | Sim bundle build với phiên bản lạ — rebuild với schema_version=1 |

Chi tiết: [troubleshooting.md](troubleshooting.md).
