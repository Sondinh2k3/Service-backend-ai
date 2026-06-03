# AI Algorithm Service

Backend AI cho điều khiển đèn giao thông. Service nhận trạng thái hiện tại từ Core Controller, chạy policy RL/GNN theo bundle đang active, trả về thời lượng pha để Core Controller quyết định có đẩy xuống TSC hay fallback fixed-time.

## What it does

- Runtime inference: `POST /api/algorithm/ai`.
- Readiness guard: chỉ inference khi area/network có active bundle hợp lệ.
- Real-network sync: nhận snapshot topology thật, compile real normalization để runtime hydrate static metadata.
- Sim-to-real compose: ghép sim bundle với real topology để tạo runtime bundle.
- Auto-sync: theo dõi MinIO/S3 và tự pull bundle mới.
- Safety: guardrails min/max green, anti-starvation, strict mode, audit log, request id.

## Architecture at a glance

```text
Core Controller
  -> AI Service / ai-runtime :8001
       POST /api/algorithm/ai
       GET  /ready

Backend / DevOps
  -> AI Service / ai-ops :8002
       PUT  /internal/sync/areas/{area_id}/real-network
       POST /ops/sim-bundles/pull
       POST /ops/bundles/{bundle_id}/activate

Vendor / Training
  -> MinIO/S3
       sim/{tenant}/{network}/.../*.sim.zip
```

Production splits the app into two service roles:

| Role | Port | Purpose |
|---|---:|---|
| `runtime` | 8001 | Public runtime API for Core Controller |
| `ops` | 8002 | Internal sync, bundle lifecycle, auto-sync |

Both roles are part of AI Algorithm Service. Core Controller only calls the runtime HTTP API.

## Quick start

```bash
cd ai-algorithm-service
docker compose up -d

curl http://localhost:8001/health
curl http://localhost:8001/ready
```

Run the full local pipeline with [docs/end-to-end-test.md](docs/end-to-end-test.md).

## Production notes

- Change the demo API key `sondinh2k3`.
- Use `AI_STRICT_MODE=true`.
- Use `SIM_BUNDLE_AUTO_ACTIVATE=false` until the compatibility report has been reviewed.
- Do not go live if `compatibility_report.json` contains `AUTO_CROSS_MAPPING_BY_ORDER`.
- `simToReal` is not exported from management DB; configure or confirm it separately.
- Compact inference should only send dynamic state/demand; static topology is hydrated from `models/real_normalization/area_<area_id>/` and the active runtime bundle.

## Project layout

```text
src/
  api/              Public runtime API and internal sync API
  ops/              Bundle lifecycle, composer, auto-sync
  runtime/          Active bundle resolver, guardrails, preflight
  services/         AI inference, sync, audit, model loading
  schemas/          Pydantic request/response schemas
  preprocessing/    Topology, phase, feature normalization
  observability/    Metrics, drift, logging helpers

docs/               Technical docs
api_docs/           Endpoint-specific API docs
postman/            Postman collection and environment
```

## Documentation

Start from [docs/README.md](docs/README.md).

Most-used docs:

- [docs/core-controller-api-contract.md](docs/core-controller-api-contract.md)
- [api_docs/run_ai_algorithm.md](api_docs/run_ai_algorithm.md)
- [docs/PIPELINE.md](docs/PIPELINE.md)
- [docs/deployment.md](docs/deployment.md)
- [docs/troubleshooting.md](docs/troubleshooting.md)

## Testing

```bash
uv run pytest
```

More details: [docs/testing.md](docs/testing.md).
