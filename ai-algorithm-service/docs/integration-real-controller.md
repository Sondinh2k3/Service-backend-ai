# Integration With Real Core Controller

This document describes how to move from lab integration to production actuation with a real Traffic Signal Controller (TSC).

## 1. Contract boundary

```text
Core Controller
  owns sensor/TSC state, validation, actuation, fallback

AI Service
  owns inference, bundle readiness, guardrails, audit
```

AI Service only proposes signal timing. Core Controller decides whether to push it to TSC.

## 2. Runtime implementation on Core Controller

Core Controller must implement:

- HTTP client for `POST /api/algorithm/ai`.
- Timeout 500 ms.
- Retry at most 1 time.
- Mapping from internal controller state to compact `AIInput`.
- Output validation before TSC.
- Fixed-time fallback path.
- Audit log with `X-Request-Id`.

Output validation should include:

```text
status == 1
sum(phase.greenTime + phase.yellowTime + phase.redClearTime) ~= cycleLength
```

Never leave TSC without a valid plan.

Runtime request should contain only dynamic data:

- `areaId`, `crossId`.
- Current `cycleId`.
- Current stage state: `stageId` + `greenTime` or `duration`.
- Road demand observations: speed, occupancy, queue, vehicle count/window.

Do not resend topology/static metadata every cycle. AI Service hydrates direction, road static, cycle/stage metadata, yellow/red-clear, and cycle length from compiled real normalization. Active runtime bundle provides the policy/model and can contribute model-specific phase mapping.

## 3. Data needed before production

### 3.1 Real topology

Backend/Core management exports:

- `area`
- `areaCrosses`
- `crosses`
- `roads`
- `cycles`
- `stages`

Recommended:

- `crosses[].location` as `"lat,lon"`.
- `roads[].coordinates` from road coordinate/polyline data.
- `cycles[].cycle_length`.
- `stages[].green`, `yellow`, `red_clear`, `stage_code`, `old_id`.
- `roads[].number_of_lanes`, `length`, `speed_design`, `capacity_design`.

### 3.2 `simToReal`

`simToReal` is separate from DB export. It maps sim/training cross IDs to real DB cross IDs.

Valid sources:

- Operator configuration.
- Mapping file from training/integration team.
- Auto-suggest result confirmed by operator.

Production must not rely on order-based fallback. If compatibility report contains `AUTO_CROSS_MAPPING_BY_ORDER`, do not go live.

### 3.3 Sync calls

When topology changes:

```http
PUT /internal/sync/areas/{area_id}/real-network
```

Before uploading/activating training bundle:

```http
GET /internal/sync/areas/{area_id}/real-normalization
```

Check `direction_map` and mapping readiness.

## 4. Phased rollout

| Phase | Goal | Actuation |
|---|---|---|
| Lab | API/schema and bundle readiness | No real TSC |
| Shadow | Compare AI plan with current controller plan | No |
| Pilot | One intersection with operator supervision | Limited |
| Expansion | More intersections/areas | Yes, with rollback |

## 5. Phase gates

### Lab -> Shadow

- Runtime and ops healthy.
- Area readiness true.
- Inference latency stable.
- Core Controller fallback works.
- Audit log contains request/response.

### Shadow -> Pilot

- AI plan passes validation for agreed period.
- No repeated timeout/fallback spike.
- Operator approves observed timing behavior.
- Rollback procedure tested.

### Pilot -> Expansion

- No safety incident.
- Drift/latency/fallback metrics acceptable.
- Bundle hot-swap and rollback tested.
- Field team signs off.

## 6. No-go criteria

Immediate fallback/rollback if:

- TSC rejects AI plan.
- AI response invalid.
- Latency exceeds agreed SLA repeatedly.
- Readiness false.
- Compatibility report has unresolved errors.
- `AUTO_CROSS_MAPPING_BY_ORDER` is present in production bundle review.
- Operator activates kill switch.

## 7. Monitoring

Track:

- `latency_ms`
- HTTP status/error code
- fallback rate
- active bundle id/version
- readiness
- guardrail violations
- drift alerts
- TSC reject count

## 8. Rollback

Use ops rollback:

```bash
curl -X POST \
  -H "X-Internal-API-Key: $INTERNAL_API_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:8002/ops/networks/<network_id>/rollback \
  -d '{"reason":"field rollback"}'
```

Core Controller fallback should work even if AI Service is completely unreachable.

## 9. Responsibilities

| Team | Responsibility |
|---|---|
| Core Controller | Runtime request, validation, TSC actuation, fallback, audit |
| Backend management | Export topology, own real DB, provide mapping workflow |
| AI/Training | Build sim bundle and policy artifacts |
| DevOps | Deploy service, configure MinIO, monitor, rollback |
| Field operator | Approve mapping, pilot, go/no-go |

## 10. References

- [core-controller-api-contract.md](core-controller-api-contract.md)
- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md)
- [deployment.md](deployment.md)
- [troubleshooting.md](troubleshooting.md)
