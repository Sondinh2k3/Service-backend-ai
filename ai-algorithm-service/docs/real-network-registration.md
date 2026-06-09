# Real Network Registration Payload

This document describes the payload accepted by:

```http
PUT /internal/sync/areas/{area_id}/real-network
```

The service supports two input shapes:

- **Flat v1**: mirrors management DB tables (`areaCrosses`, `crosses`, `roads`, `cycles`, `stages`).
- **Nested v2**: groups child data under each cross. The API adapter flattens it internally before saving and compiling `real_normalization.json`.

The internal pipeline is unchanged: both shapes are compiled into the same runtime files under `models/real_normalization/area_<area_id>/`.

## Recommended Shape

Use nested v2 for new integrations:

```json
{
  "sourceEventId": "evt-real-network-leductho-20260608",
  "tenantId": "hanoi_pilot",
  "networkId": "leductho",
  "schemaVersion": "real-network/v2",
  "area": {
    "id": 1308701,
    "name": "Le Duc Tho Corridor"
  },
  "crosses": [
    {
      "id": 33000000201001,
      "simId": "cluster10123839429_1312668863_1312668913_1720251781_#8more",
      "location": "21.038294,105.772069",
      "primaryCycleId": 2001,
      "roads": [
        {
          "id": 710001,
          "direction": 1,
          "toCrossId": null,
          "lanes": 3,
          "length": 14,
          "capacity": 5400,
          "speedDesign": 50,
          "coordinates": [
            [21.039016, 105.772072],
            [21.038294, 105.772069]
          ]
        }
      ],
      "cycles": [
        {
          "id": 2001,
          "type": 0,
          "length": 138,
          "yellow": 3,
          "redClear": 1,
          "stages": [
            {
              "id": 99101,
              "order": 1,
              "oldId": "0",
              "green": 65,
              "yellow": 3,
              "redClear": 1,
              "minGreen": 15,
              "maxGreen": 90
            }
          ]
        }
      ]
    }
  ]
}
```

If `simToReal` is omitted, the adapter derives it from `crosses[].simId`:

```json
{
  "simToReal": {
    "cluster10123839429_1312668863_1312668913_1720251781_#8more": 33000000201001
  }
}
```

You can still send `simToReal` explicitly. Explicit values are preserved.

## Required Data

Top-level:

| Field | Required | Used for |
|---|---:|---|
| `sourceEventId` | yes | Idempotency |
| `tenantId` | recommended | Bundle/network ownership |
| `networkId` | recommended | Bundle lookup and active runtime bundle |
| `schemaVersion` | recommended | Payload versioning |
| `area.id` | yes | Area identity |
| `area.name` | recommended | Area display/debug metadata |

Cross:

| Field | Required | Used for |
|---|---:|---|
| `id` | yes | Real cross ID |
| `simId` | recommended | Derive `simToReal` |
| `location` or `centerCoordinate` | recommended | GPS-based direction inference |
| `primaryCycleId` | recommended | Primary cycle selection |

Road:

| Field | Required | Used for |
|---|---:|---|
| `id` | yes | Real road ID used by runtime observations |
| `direction` | fallback | Direction inference when GPS is missing |
| `toCrossId` | optional | Neighbor graph |
| `lanes` | recommended | Observation mask/static feature metadata |
| `length` | recommended | Static feature metadata |
| `capacity` | recommended | Saturation flow/static metadata |
| `speedDesign` | recommended | Speed normalization |
| `coordinates` | recommended | GPS-based direction inference |

Cycle:

| Field | Required | Used for |
|---|---:|---|
| `id` | yes | Cycle identity |
| `type` | optional | Fallback primary cycle selection |
| `length` | recommended | Runtime cycle hydration |
| `yellow` | recommended | Runtime timing hydration |
| `redClear` | recommended | Runtime timing hydration |

Stage:

| Field | Required | Used for |
|---|---:|---|
| `id` | yes | Real stage ID |
| `order` | yes | Stage ordering and standard phase mapping |
| `oldId` | recommended | Debug/sim phase traceability |
| `green` | recommended | Runtime duration hydration |
| `yellow` | recommended | Runtime timing hydration |
| `redClear` | recommended | Runtime timing hydration |
| `minGreen` | optional | Guardrail/static metadata |
| `maxGreen` | optional | Guardrail/static metadata |

## What Can Be Omitted

For nested v2, omit these fields:

- Repeated parent IDs such as `area_id`, `cross_id`, and `cycle_id`.
- `is_active`, if the payload only contains active entities.
- `number_of_stages`; the service counts stages from the payload.
- `toCrossDirection` when `toCrossId` is null.

## Internal Flattening

The adapter converts nested v2 into the existing flat v1 snapshot:

| Nested v2 | Internal flat field |
|---|---|
| `area.id` | `area.area_id` |
| `area.name` | `area.area_name` |
| `cross.primaryCycleId` | `areaCrosses[].cycle_id` |
| `cross.roads[]` | `roads[]` |
| `cross.cycles[]` | `cycles[]` |
| `cycle.stages[]` | `stages[]` |
| `cross.simId` | `simToReal[simId] = cross.id` |

This keeps the runtime behavior and compiler logic unchanged.
