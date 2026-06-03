# Architecture

AI Algorithm Service là lớp AI backend nằm giữa Core Controller và pipeline training/RLOps.

## 1. High-level view

```text
Core Controller
  -> AI Service / ai-runtime
       inference, readiness, guardrails

Backend / Integration
  -> AI Service / ai-ops
       real topology sync, bundle lifecycle

Training / Vendor
  -> MinIO/S3
       sim bundle artifacts
```

`ai-runtime` và `ai-ops` đều là component của AI Algorithm Service backend. Core Controller chỉ là client gọi API runtime.

## 2. Service roles

| Role | Router | Main responsibilities |
|---|---|---|
| `runtime` | public runtime API | `POST /api/algorithm/ai`, readiness, active bundle hot reload |
| `ops` | internal sync + ops API | topology sync, real normalization, sim bundle compose, activate, rollback |
| `all` | both | Local/dev only |

Production should run `runtime` and `ops` separately.

## 3. Runtime path

```text
AIInput
  -> validate schema
  -> resolve active bundle by area/network
  -> build features from real observation
  -> run policy
  -> map action to signal plan
  -> guardrails
  -> AIOutput
```

Important runtime modules:

| Module | Purpose |
|---|---|
| `src/api/ai.py` | Runtime HTTP endpoints |
| `src/services/ai_service.py` | Main inference orchestration |
| `src/services/model_manager.py` | Load/cache policy and metadata |
| `src/runtime/bundle_resolver.py` | Resolve active runtime bundle |
| `src/runtime/guardrails.py` | Min/max green and safety constraints |
| `src/services/audit_service.py` | Inference audit |

## 4. Ops path

```text
Real network snapshot
  -> models/real_normalization/area_<area_id>/
       real_normalization.json
       network.json
       intersections/cross_<cross_id>.json

Sim bundle
  -> compatibility check
  -> deployment_map.json
  -> runtime bundle
  -> active pointer
```

Important ops modules:

| Module | Purpose |
|---|---|
| `src/api/internal_sync.py` | Internal sync endpoints |
| `src/services/sync_service.py` | Area/config/snapshot persistence |
| `src/ops/real_normalization.py` | Convert DB snapshot to runtime shape |
| `src/ops/composer.py` | Compose sim bundle + real topology |
| `src/ops/lifecycle.py` | Pull/register/activate/rollback bundles |
| `src/ops/auto_sync.py` | MinIO listener and safety-net poller |

## 5. Bundle layout

Runtime bundle is stored under local model storage:

```text
models/
  networks/
    <network_id>/
      active.json
      bundles/
        <bundle_id>/
          policy.onnx
          policy_meta.json
          network.json
          deployment_map.json
          compatibility_report.json
```

`active.json` selects the bundle used by `ai-runtime`.

Compiled real normalization is stored separately from the runtime bundle:

```text
models/
  real_normalization/
    area_<area_id>/
      real_normalization.json
      network.json
      intersections/
        cross_<cross_id>.json
```

Runtime static metadata lookup prefers real normalization first, then active runtime bundle metadata, then legacy area config.

## 6. Real topology and mapping

Real topology comes from Core/backend management DB:

- `area`
- `areaCrosses`
- `crosses`
- `roads`
- `cycles`
- `stages`

`simToReal` is not part of that DB export. It is an overlay mapping from sim/training cross IDs to real DB cross IDs. Production must provide or confirm it separately.

See [sim-to-real-mapping.md](sim-to-real-mapping.md).

## 7. Safety layers

| Layer | Protection |
|---|---|
| Readiness | No inference if area/bundle is not ready |
| Strict mode | Fail fast on missing config/model |
| Guardrails | Clamp unsafe phase duration |
| Core fallback | Fixed-time plan if AI fails |
| Audit | Trace input/output with request id |
| Rollback | Reactivate previous bundle |

## 8. Observability

- `/metrics` exposes Prometheus metrics.
- `X-Request-Id` flows through logs.
- Drift detector tracks input distribution changes.
- Bundle events record lifecycle operations.

## 9. References

- [PIPELINE.md](PIPELINE.md)
- [api-reference.md](api-reference.md)
- [configuration.md](configuration.md)
