# Sim-to-Real Mapping

`simToReal` là cầu nối giữa topology mô phỏng/training và topology thật trong DB/Core Controller.

## 1. Three ID spaces

```text
SUMO / training ID
  -> standard runtime phase/direction
  -> real DB cross/road/cycle/stage ID
```

| Space | Example | Owner |
|---|---|---|
| Sim ID | `33202549` | Training/SUMO |
| Standard runtime | phase index `0..7`, direction `N/E/S/W` | AI Service |
| Real DB ID | `cross_id=567001`, `road_id=9001` | Core/backend management |

## 2. What `simToReal` is

Example:

```json
{
  "simToReal": {
    "33202549": 567001,
    "360082": 567002
  }
}
```

It maps:

```text
sim cross/TLS ID -> real DB cross ID
```

It is not available in `management.sql`. The management DB only knows real IDs. Production must provide this mapping separately.

## 3. Valid production sources

- Operator config in an integration UI.
- Mapping file delivered by training/integration team.
- Auto-suggest tool using name, `old_id`, GPS, topology similarity, then operator confirmation.
- Explicit `sim_tls_id` or `sim_cross_id` attached to each real cross, if that field is intentionally added and confirmed.

## 4. Composer priority

The composer resolves mapping in this order:

1. Explicit `simToReal` / `sim_to_real` / cross map.
2. Confirmed `sim_tls_id` or `sim_cross_id` on real crosses.
3. Order-based fallback when sim cross count equals real cross count.
4. Fail if mapping cannot be resolved.

Order-based fallback writes warning `AUTO_CROSS_MAPPING_BY_ORDER`.

## 5. Production rule

Do not activate a production runtime bundle if:

- `simToReal` is missing or unconfirmed.
- `compatibility_report.json` has error.
- `compatibility_report.json` has warning `AUTO_CROSS_MAPPING_BY_ORDER`.

That warning is acceptable only for demo/dev because DB export order can change and map the wrong intersection.

## 6. Related files

- [PIPELINE.md](PIPELINE.md)
- [core-controller-api-contract.md](core-controller-api-contract.md)
- [troubleshooting.md](troubleshooting.md)
