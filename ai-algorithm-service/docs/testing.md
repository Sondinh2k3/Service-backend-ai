# Testing

## Quick Run

```bash
uv sync --extra dev
APP_ENV=test DEBUG=false UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy \
  uv run pytest tests -q
```

PowerShell:

```powershell
uv sync --extra dev
$env:APP_ENV="test"
$env:DEBUG="false"
$env:UV_CACHE_DIR="/tmp/uv-cache"
$env:UV_LINK_MODE="copy"
uv run pytest tests -q
```

Nếu `.env.development` hoặc shell local có env lạ, set `APP_ENV=test DEBUG=false` như trên để Pydantic không đọc nhầm cấu hình dev.

## Test Files

| File | Coverage |
|---|---|
| [tests/test_sim_bundle_pipeline.py](../tests/test_sim_bundle_pipeline.py) | Sim Bundle reader legacy alias, deployment map composer, real_normalization từ service-owned snapshot |
| [tests/test_runtime_v2.py](../tests/test_runtime_v2.py) | Cycle/stage-id phase mapping, FeatureBuilder, runtime v2 behavior |
| [tests/test_extractor_v2.py](../tests/test_extractor_v2.py) | Runtime Bundle extract/validate, checksum tamper detection |
| [tests/test_guardrails.py](../tests/test_guardrails.py) | Min/max green, masked stages, anti-starvation |
| [tests/test_topology_hash.py](../tests/test_topology_hash.py) | Deterministic topology hash |
| [tests/test_apis.py](../tests/test_apis.py) | API smoke tests |

## Focused Pipeline Test

```bash
env APP_ENV=test DEBUG=false UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy \
  uv run pytest tests/test_sim_bundle_pipeline.py tests/test_runtime_v2.py tests/test_extractor_v2.py -q
```

Expected: tất cả pass (chính xác số test thay đổi theo phiên bản — verify bằng `pytest --collect-only`).

## Known issues

- `tests/test_guardrails.py::test_max_green_clip` hiện fail do max_green clip logic chưa khớp test expectation. Pre-existing, không liên quan pipeline sim-to-real. Skip bằng:

```bash
uv run pytest tests/ --deselect tests/test_guardrails.py::test_max_green_clip -q
```

## Notes

- Full API tests có thể cần isolated DB hoặc env sạch nếu chạy trong IDE có env cũ.
- `bundle-tooling` và `traffic_rl_features` phải được cài editable từ sibling directories.
- Test không cần Docker trừ khi bạn muốn chạy end-to-end MinIO/MySQL thật theo [end-to-end-test.md](end-to-end-test.md).

