# Troubleshooting

Use this as a runbook. Start with the symptom, run the checks, then apply the fix.

## 1. First checks

```bash
docker compose ps
curl http://localhost:8001/health
curl http://localhost:8001/ready
curl http://localhost:8002/health
```

Check logs:

```bash
docker compose logs -f ai-runtime
docker compose logs -f ai-ops
```

## 2. Setup and Docker

| Symptom | Cause | Fix |
|---|---|---|
| `No services to build` | Wrong directory or compose file | `cd ai-algorithm-service` |
| Container restarts | Env/config/startup preflight failure | `docker compose logs ai-runtime ai-ops` |
| Missing Python module in exec | Running outside managed env | Use `uv run ...` or exec inside the right container |

## 3. Auth

### `401 UNAUTHORIZED`

Internal sync and ops endpoints need:

```http
X-Internal-API-Key: <INTERNAL_API_KEY>
```

Check:

```bash
docker compose exec ai-ops env | grep INTERNAL_API_KEY
```

The demo key in local compose is usually `sondinh2k3`; production must use a strong unique key.

## 4. Readiness and inference

### `AREA_NOT_READY`

Check:

```bash
curl http://localhost:8001/api/algorithm/ai/areas/1/readiness
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/networks/<network_id>/active
```

Common causes:

- No active bundle.
- Missing `policy.onnx`, `policy_meta.json`, or `network.json`.
- Active bundle points to wrong network.
- Runtime cache has not reloaded yet.

Fix:

- Activate valid bundle.
- Rollback if latest bundle is bad.
- Clear cache if needed:

```bash
curl -X POST http://localhost:8001/api/algorithm/ai/cache/clear
```

### `422 Unprocessable Entity`

Payload does not match `AIInput`. Compare with [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

Common issues:

- Missing `crosses`.
- Wrong field casing.
- Null values where schema expects number/list.
- Mixed areas in one request when strict mode is enabled.

### Latency high

Check:

- CPU saturation.
- Bundle/model size.
- Number of crosses per request.
- Container resource limits.
- Logs around model reload/cache miss.

Core Controller must fallback if runtime exceeds timeout.

## 5. Real snapshot and normalization

### `DIRECTION_MISSING_IN_REAL`

Check real normalization:

```bash
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/internal/sync/areas/1/real-normalization
```

Fix:

- Add `crosses[].location` and `roads[].coordinates`.
- Or verify legacy direction encoding.
- Re-sync snapshot and recompile.

### Output phase ratio looks wrong

If readiness is true and inference latency is normal, check mapping:

- Is `simToReal` correct?
- Does each sim cross map to the intended real cross?
- Does report contain `AUTO_CROSS_MAPPING_BY_ORDER`?

Production must not activate a bundle with order-based mapping warning.

## 6. Bundle lifecycle

### Bundle uploaded but not deployed

Check:

```bash
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/auto-sync/status

curl -X POST -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/auto-sync/scan-now
```

Common causes:

- Wrong MinIO prefix/suffix.
- Wrong bucket/credentials.
- Listener disconnected.
- Bundle already known.
- Sim bundle waiting for real snapshot.

### Bundle status `pending_real_snapshot`

Meaning: sim bundle arrived before real topology for matching `tenantId/networkId`.

Fix:

```bash
curl -X PUT http://localhost:8002/internal/sync/areas/1/real-network \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  -d @real_snapshot.json
```

Service should retry compose after snapshot sync.

### Compose failed

Open/review `compatibility_report.json` in the bundle directory or inspect bundle detail/events.

Common codes:

| Code | Fix |
|---|---|
| `SIM_CROSS_NOT_MAPPED` | Add/fix `simToReal` |
| `REAL_CROSS_NOT_FOUND` | Mapping points to non-existent real cross |
| `STAGE_COUNT_MISMATCH` | Align sim phases with real cycle stages |
| `DIRECTION_MISSING_IN_REAL` | Add GPS/coordinates or fix direction encoding |
| `AUTO_CROSS_MAPPING_BY_ORDER` | Production blocker; add explicit mapping |

## 7. Auto-sync

| Symptom | Check |
|---|---|
| Listener not alive | MinIO endpoint, credentials, bucket notification |
| Scan finds nothing | Prefix/suffix and bucket path |
| Pull fails repeatedly | Object exists, permissions, network |
| Wrong customer affected | Prefix must be scoped per tenant/network |

See [auto-sync.md](auto-sync.md).

## 8. Rollback

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/networks/<network_id>/rollback \
  -d '{"reason":"manual rollback"}'
```

Then check active bundle:

```bash
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/networks/<network_id>/active
```

## 9. Observability

### Prometheus has no data

```bash
curl http://localhost:8001/metrics
curl http://localhost:8002/metrics
```

Check scrape config and target health.

### Logs do not correlate

Ensure caller sends:

```http
X-Request-Id: <trace-id>
```

## 10. Debug commands

```bash
docker compose ps
docker compose logs -f ai-runtime
docker compose logs -f ai-ops
curl http://localhost:8001/ready
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" http://localhost:8002/ops/bundles
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" http://localhost:8002/ops/auto-sync/status
```

## 11. References

- [api-reference.md](api-reference.md)
- [core-controller-api-contract.md](core-controller-api-contract.md)
- [sim-to-real-mapping.md](sim-to-real-mapping.md)
