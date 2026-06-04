# End-to-End Test

Goal: verify the full local flow.

```text
Docker stack -> real snapshot -> real normalization -> sim bundle -> runtime bundle -> compact inference
```

## 1. Prerequisites

- Docker and Docker Compose.
- `uv` if running Python utilities locally.
- Optional: MinIO client `mc`.
- Repo checked out with `ai-algorithm-service`.

## 2. Start stack

```bash
cd ai-algorithm-service
docker compose up -d
docker compose ps
```

Check probes:

```bash
curl http://localhost:8001/health
curl http://localhost:8001/ready
curl http://localhost:8002/health
```

`/ready` may be false before bundle activation; that is expected.

## 3. Sync real network snapshot

Fast path, using the project script if available:

```bash
uv run python scripts/register_demo_real_network_snapshot.py
```

Manual path:

```bash
curl -X PUT http://localhost:8002/internal/sync/areas/1/real-network \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: sondinh2k3" \
  -d @dist/full_real_network_snapshot.example.json
```

Production note:

- DB export provides real topology only.
- `simToReal` must be configured or confirmed separately.
- Do not rely on order-based mapping for production.

## 4. Verify real normalization

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/internal/sync/areas/1/real-normalization
```

Check:

- `crosses` exists.
- Each cross has a `direction_map`.
- Cycles/stages include static timing such as `cycle_length`, `yellow`, and `red_clear`.
- `sim_to_real` is present/confirmed for production.

Recompile if needed:

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/internal/sync/areas/1/real-normalization/recompile
```

## 5. Upload or pull sim bundle

If auto-sync is configured, upload the sim bundle to the configured MinIO prefix.

If testing manually, call:

```bash
curl -X POST http://localhost:8002/ops/sim-bundles/pull \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: sondinh2k3" \
  -d '{
    "sourceUri": "s3://ai-models/sim/default/cologne3/cologne3.sim.zip",
    "activate": false
  }'
```

Use the actual URI from your local MinIO/test setup.

## 6. Review and activate bundle

List bundles:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/bundles
```

Activate after report review:

```bash
curl -X POST -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/bundles/<bundle_id>/activate
```

Production blockers:

- Any compatibility error.
- Warning `AUTO_CROSS_MAPPING_BY_ORDER`.
- Missing/unclear `simToReal`.

## 7. Verify runtime readiness

```bash
curl http://localhost:8001/ready
curl http://localhost:8001/api/algorithm/ai/areas
curl http://localhost:8001/api/algorithm/ai/areas/1/readiness
```

Expected after activation: service ready and area ready.

## 8. Inference test

Use the example payload [api-payload-examples/inference-compact-request.json](api-payload-examples/inference-compact-request.json), the Postman collection, or send a compact runtime payload that matches [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md):

```bash
curl -X POST http://localhost:8001/api/algorithm/ai \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: e2e-demo-001" \
  -d '{
    "areaId": 1,
    "crosses": [
      {
        "crossId": 567001,
        "cycleId": 1,
        "stages": [
          {"stageId": 1, "greenTime": 40},
          {"stageId": 2, "greenTime": 40}
        ],
        "roads": [
          {
            "roadId": 1,
            "averageSpeed": 0,
            "occupancySpace": 0,
            "totalVehicle": 0,
            "windowSeconds": 60,
            "queueLength": 0,
            "density": 0
          }
        ]
      }
    ]
  }'
```

Validate:

- HTTP `200`.
- `status == 1`.
- Response has all expected crosses.
- Cycle duration is valid: `sum(greenTime + yellowTime + redClearTime) ~= cycleLength`.

## 9. Race-condition test

Test sim bundle before real snapshot:

1. Remove/reset current snapshot and active bundle in your local test environment.
2. Pull/upload sim bundle first.
3. Verify bundle status is `pending_real_snapshot`.
4. Sync real snapshot.
5. Verify service retries compose.
6. Activate bundle after review.

Expected behavior: no crash, no invalid activation before real snapshot is available.

## 10. Rollback test

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/<network_id>/rollback \
  -d '{"reason":"e2e rollback test"}'
```

Then verify:

```bash
curl -H "X-Internal-API-Key: sondinh2k3" \
  http://localhost:8002/ops/networks/<network_id>/active
```

## 11. Quick troubleshooting

| Symptom | Next check |
|---|---|
| `401` on ops/sync | `X-Internal-API-Key` |
| `AREA_NOT_READY` | area readiness and active bundle |
| Bundle pending | real snapshot and `tenantId/networkId` |
| Compose failed | compatibility report |
| Direction missing | GPS/road direction data |
| Output strange | `simToReal` mapping |

Full guide: [troubleshooting.md](troubleshooting.md).

## 12. Production adaptation

For a real network:

1. Use real `tenantId/networkId`.
2. Export real topology from management DB.
3. Add confirmed `simToReal`.
4. Keep `SIM_BUNDLE_AUTO_ACTIVATE=false`.
5. Review report before activation.
6. Run shadow mode before actuation.

## 13. References

- [PIPELINE.md](PIPELINE.md)
- [core-controller-api-contract.md](core-controller-api-contract.md)
- [deployment.md](deployment.md)
