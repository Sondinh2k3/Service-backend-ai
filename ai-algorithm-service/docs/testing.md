# Testing

## Quick run

```bash
cd ai-algorithm-service
uv run pytest
```

With verbose output:

```bash
uv run pytest -vv
```

Run one test file:

```bash
uv run pytest tests/test_real_network_sync.py -vv
```

## Test groups

| Area | What to check |
|---|---|
| API schemas | Pydantic validation and error contract |
| Sync service | Area, cross config, real network snapshot |
| Composer | Sim bundle -> runtime bundle compatibility |
| Runtime | Readiness, inference, guardrails |
| Ops | Bundle pull, activate, rollback, auto-sync |

## End-to-end test

Use [end-to-end-test.md](end-to-end-test.md) for the Docker/MinIO/runtime flow.

## Notes

- If local dependencies are missing, run `uv sync` first.
- For Docker-based checks, start the stack with `docker compose up -d`.
- Keep production mapping checks in tests whenever changing composer logic: explicit `simToReal` should pass; order fallback should produce `AUTO_CROSS_MAPPING_BY_ORDER`.
