# API Reference

## 1. Base URLs

| Service | Local URL | Purpose |
|---|---|---|
| ai-runtime | `http://localhost:8001` | Runtime inference |
| ai-ops | `http://localhost:8002` | Sync and bundle lifecycle |

Internal/ops APIs require:

```http
X-Internal-API-Key: <INTERNAL_API_KEY>
```

All callers should send:

```http
X-Request-Id: <trace-id>
```

## 2. Common probes

### `GET /health`

Liveness probe.

### `GET /ready`

Readiness probe. Runtime can be alive but not ready if active bundle/config is missing.

### `GET /metrics`

Prometheus metrics.

## 3. Runtime APIs

### `GET /api/algorithm/ai/areas`

Returns visible/ready areas.

### `GET /api/algorithm/ai/areas/{area_id}/readiness`

Returns readiness detail for one area.

### `GET /api/algorithm/ai/areas/{area_id}/network`

Returns runtime `network.json` for an area.

### `GET /api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config`

Returns intersection runtime config.

### `PUT /api/algorithm/ai/areas/{area_id}/intersections/{cross_id}/config`

Manual config override. Prefer internal sync APIs for production.

### `POST /api/algorithm/ai`

Main inference endpoint.

Production callers should send compact runtime state only: `areaId`, `crossId`, current cycle/stage state, and road demand observations. Static topology is hydrated from the synced snapshot/runtime bundle.

See [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md).

### `POST /api/algorithm/ai/cache/clear`

Clears in-memory config/model cache. Optional query:

```text
?area_id=<id>
```

## 4. Internal sync APIs

### `PUT /internal/sync/areas/{area_id}`

Upsert area metadata.

```json
{
  "sourceEventId": "area-1-v1",
  "tenantId": "tenant_kh1",
  "networkId": "network_hn_001",
  "areaName": "Area 1",
  "isActive": true,
  "controllerVisible": true
}
```

### `PUT /internal/sync/areas/{area_id}/real-network`

Sync real topology snapshot and compile `real_normalization.json`.

Production payload includes real topology from DB plus confirmed `simToReal` overlay:

```json
{
  "sourceEventId": "real-network-1-v1",
  "tenantId": "tenant_kh1",
  "networkId": "network_hn_001",
  "schemaVersion": "real-network-v1",
  "sourceVersion": "2026-06-02T10:00:00+07:00",
  "area": {},
  "areaCrosses": [],
  "crosses": [],
  "roads": [],
  "cycles": [],
  "stages": [],
  "simToReal": {
    "sim_cross_id": 1001
  }
}
```

Important:

- `simToReal` is not exported from management DB.
- `cycles/stages/roads` should include enough static metadata for runtime hydrate: cycle length, stage yellow/red-clear, road lanes/length/speed/capacity.
- Do not activate production bundle if mapping falls back to order.

### `GET /internal/sync/areas/{area_id}/real-normalization`

Read compiled real normalization.

### `POST /internal/sync/areas/{area_id}/real-normalization/recompile`

Recompile from the stored snapshot.

### `PUT /internal/sync/areas/{area_id}/crosses/{cross_id}/config`

Sync one cross config.

### `POST /internal/sync/finalize`

Finalize sync for selected areas.

## 5. Deprecated legacy artifact APIs

These exist for older flows and should not be used for new production integrations:

- `PUT /internal/sync/areas/{area_id}/artifacts`
- `POST /internal/sync/areas/{area_id}/artifacts/{artifact_id}/activate`

Use sim bundle -> runtime bundle lifecycle instead.

## 6. Ops APIs

### `GET /ops/bundles`

List bundles. Query filters may include network/status depending on current implementation.

### `GET /ops/bundles/{bundle_id}`

Get bundle detail.

### `POST /ops/sim-bundles/pull`

Pull sim bundle and compose runtime bundle.

```json
{
  "sourceUri": "s3://ai-models/sim/tenant_kh1/network_hn_001/model.sim.zip",
  "activate": false
}
```

### `POST /ops/bundles/{bundle_id}/activate`

Activate a reviewed bundle.

### `POST /ops/networks/{network_id}/rollback`

Rollback active bundle for a network.

### `GET /ops/networks/{network_id}/active`

Return active bundle pointer.

### `GET /ops/auto-sync/status`

Return listener/poller status.

### `POST /ops/auto-sync/scan-now`

Force a MinIO/S3 scan.

### `GET /ops/bundles/{bundle_id}/events`

Return lifecycle events for one bundle.

## 7. Error response

Application errors use:

```json
{
  "error": {
    "code": "AREA_NOT_READY",
    "message": "Area is not ready",
    "requestId": "..."
  }
}
```

Common codes:

| Code | Typical status | Meaning |
|---|---:|---|
| `INVALID_INPUT` | 400/422 | Payload invalid |
| `CONFIG_NOT_FOUND` | 404/409 | Missing config/network |
| `MODEL_NOT_FOUND` | 404/409 | Missing model/bundle |
| `AREA_NOT_READY` | 409 | Area not ready for inference |
| `MULTIPLE_AREAS_NOT_ALLOWED` | 400 | Request contains multiple areas while strict |
| `UNAUTHORIZED` | 401 | Missing/wrong internal API key |

## 8. References

- [core-controller-api-contract.md](core-controller-api-contract.md)
- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md)
- [configuration.md](configuration.md)
