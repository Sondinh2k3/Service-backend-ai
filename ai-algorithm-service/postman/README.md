# Postman Collection

This folder contains a Postman collection and environment for local demos and integration checks.

## Files

| File | Purpose |
|---|---|
| `RLOps E2E.postman_collection.json` | API requests grouped by flow |
| `RLOps Local.postman_environment.json` | Local environment variables |

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `runtime_base_url` | `http://localhost:8001` | ai-runtime |
| `ops_base_url` | `http://localhost:8002` | ai-ops |
| `internal_api_key` | `sondinh2k3` | Demo key only |
| `area_id` | `1` | Demo area |
| `network_id` | `cologne3` | Demo network |
| `request_id` | generated/manual | Trace id |

Production must not reuse the demo API key.

## Recommended flow

1. `GET /health` on runtime and ops.
2. `PUT /internal/sync/areas/{area_id}`.
3. `PUT /internal/sync/areas/{area_id}/real-network`.
4. `GET /internal/sync/areas/{area_id}/real-normalization`.
5. Pull or wait for sim bundle.
6. Review bundle/report.
7. Activate bundle.
8. `GET /ready`.
9. `POST /api/algorithm/ai`.

## Notes

- Internal and ops requests need `X-Internal-API-Key`.
- Runtime inference should include `X-Request-Id`.
- Production inference should use the compact payload: topology is synced once, runtime sends only signal state and traffic demand.
- If a request fails with `401`, check the environment key.
- If inference returns `AREA_NOT_READY`, follow [../docs/troubleshooting.md](../docs/troubleshooting.md).
