# Auto-Sync

Auto-sync lets `ai-ops` detect new bundles in MinIO/S3 and register them automatically.

## 1. Mechanism

Two mechanisms run together:

| Mechanism | Purpose |
|---|---|
| Listener | Near-real-time MinIO bucket notification |
| Safety-net poller | Periodic scan to catch missed events |

This keeps the edge customer environment outbound-only; no public webhook is required.

## 2. Required settings

```env
MINIO_ENABLED=true
MINIO_ENDPOINT=<host:port>
MINIO_ACCESS_KEY=<read-only-access-key>
MINIO_SECRET_KEY=<secret>
MINIO_BUCKET=ai-models
MINIO_AUTO_SYNC_ENABLED=true
MINIO_AUTO_SYNC_PREFIX=sim/tenant_kh1/network_001
MINIO_AUTO_SYNC_SUFFIX=.sim.zip
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_AUTO_ACTIVATE=false
```

Production should keep `SIM_BUNDLE_AUTO_ACTIVATE=false` so an operator can review `compatibility_report.json`.

## 3. Vendor workflow

```bash
# In training/vendor environment
build-sim-bundle --tenant tenant_kh1 --network network_001
mc cp model.sim.zip vendor/ai-models/sim/tenant_kh1/network_001/model.sim.zip
```

After upload, ai-ops listener should detect the object.

## 4. Edge prerequisites

Before a sim bundle can compose successfully:

- Area metadata exists.
- Real network snapshot exists for the same `tenantId/networkId`.
- Real normalization compiles.
- `simToReal` mapping is explicit or confirmed.

If the sim bundle arrives first, it becomes `pending_real_snapshot` and is retried after the snapshot is synced.

## 5. Status and manual scan

```bash
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/auto-sync/status

curl -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/auto-sync/scan-now
```

## 6. Activation policy

| Environment | Suggested activation |
|---|---|
| Local/demo | Auto-activate ok |
| Staging | Auto-compose, manual activate |
| Production | Manual activate after report review |

Production blockers:

- Any compatibility error.
- Warning `AUTO_CROSS_MAPPING_BY_ORDER`.
- Missing or unconfirmed `simToReal`.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Listener not alive | MinIO credentials, endpoint, bucket notification support |
| Bundle not detected | Prefix/suffix, bucket path, scan-now |
| Bundle pending | Real snapshot missing or `tenantId/networkId` mismatch |
| Compose failed | `compatibility_report.json`, ai-ops logs |
| Wrong customer bundle | Prefix must be scoped per tenant/network |

More: [troubleshooting.md](troubleshooting.md).
