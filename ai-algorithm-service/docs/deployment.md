# Deployment

Recommended production model: vendor builds and hosts artifacts; customer edge runs AI Service close to the Core Controller.

## 1. Roles

| Side | Owns |
|---|---|
| Vendor/training | Training, sim bundle, artifact store, release process |
| Customer edge | ai-runtime, ai-ops, local DB/model storage, Core Controller integration |
| Core/backend management | Real topology export, `simToReal` mapping workflow |

## 2. Edge topology

```text
Customer LAN
  Core Controller
    -> AI Service / ai-runtime :8001
  Backend/DevOps
    -> AI Service / ai-ops :8002
  ai-ops
    -> outbound HTTPS/VPN to vendor MinIO
```

## 3. Hardware baseline

| Component | Minimum |
|---|---|
| CPU | 4 vCPU |
| RAM | 8 GB |
| Disk | 50 GB SSD |
| GPU | Not required for ONNX CPU deployment |
| LAN latency Core -> AI | Prefer RTT < 20 ms |

## 4. Production environment

Use separate runtime and ops services.

```env
APP_ENV=production
AI_STRICT_MODE=true
INTERNAL_API_KEY=<strong-per-customer-key>
MODEL_DIR=/var/lib/ai-algorithm-service/models
DATABASE_URL=sqlite:////var/lib/ai-algorithm-service/ai_service.db

MINIO_ENABLED=true
MINIO_ENDPOINT=minio.vendor.example
MINIO_SECURE=true
MINIO_BUCKET=ai-models
MINIO_ACCESS_KEY=<read-only-key>
MINIO_SECRET_KEY=<secret>

MINIO_AUTO_SYNC_ENABLED=true
SIM_BUNDLE_AUTO_COMPOSE_ENABLED=true
SIM_BUNDLE_AUTO_ACTIVATE=false
```

Runtime container:

```env
SERVICE_ROLE=runtime
```

Ops container:

```env
SERVICE_ROLE=ops
RUNTIME_INTERNAL_URL=http://ai-runtime:8000
```

## 5. Customer setup

```bash
cd ai-algorithm-service
docker compose up -d

curl http://localhost:8001/health
curl http://localhost:8001/ready
curl -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  http://localhost:8002/ops/auto-sync/status
```

## 6. Go-live sequence

1. Start edge services.
2. Sync area metadata.
3. Sync real network snapshot.
4. Verify real normalization.
5. Upload/pull sim bundle.
6. Review `compatibility_report.json`.
7. Activate runtime bundle.
8. Check runtime readiness.
9. Run shadow mode.
10. Enable actuate gradually.

Do not go live if `AUTO_CROSS_MAPPING_BY_ORDER` appears in the report.

## 7. Real topology and mapping

Backend export from DB:

- `area`
- `areaCrosses`
- `crosses`
- `roads`
- `cycles`
- `stages`

`simToReal` must be configured or confirmed separately. It is not in management DB.

## 8. Rollback

```bash
curl -X POST \
  -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:8002/ops/networks/<network_id>/rollback \
  -d '{"reason":"rollback after field issue"}'
```

Core Controller must keep fixed-time fallback independent of rollback.

## 9. Operational checklist

- Strong internal API key.
- Persistent model/data volumes.
- Time sync on edge host.
- `X-Request-Id` required in Core Controller.
- Fallback fixed-time configured.
- Alerts for readiness false, fallback rate, latency, drift, guardrail violations.
- Manual activation in production.

## 10. References

- [configuration.md](configuration.md)
- [auto-sync.md](auto-sync.md)
- [integration-real-controller.md](integration-real-controller.md)
