# AI Algorithm Service Docs

Bộ tài liệu này mô tả cách chạy, tích hợp và vận hành AI Algorithm Service trong pipeline:

```text
Training sim bundle -> Real network snapshot -> Runtime bundle -> Inference
```

## Doc map

| Nhu cầu | Đọc nên đọc |
|---|---|
| Mới onboard | [PIPELINE.md](PIPELINE.md), sau đó [architecture.md](architecture.md) |
| Chạy demo end-to-end local | [end-to-end-test.md](end-to-end-test.md) |
| Viết Core Controller gọi AI Service | [core-controller-api-contract.md](core-controller-api-contract.md), [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md) |
| Tra endpoint | [api-reference.md](api-reference.md) |
| Deploy production | [deployment.md](deployment.md), [configuration.md](configuration.md), [auto-sync.md](auto-sync.md) |
| Debug lỗi | [troubleshooting.md](troubleshooting.md) |
| Hiểu sim -> real mapping | [sim-to-real-mapping.md](sim-to-real-mapping.md) |
| Chạy test code | [testing.md](testing.md) |
| Xem log bằng ELK local | [elk-quickstart.md](elk-quickstart.md) |

## Quick production rules

- Core Controller chỉ actuate đèn khi output hợp lệ; AI Service chỉ đề xuất plan.
- `POST /api/algorithm/ai` nên có timeout 500 ms, retry tối đa 1 lần, và fallback fixed-time.
- Mỗi request runtime nên có `X-Request-Id` để audit input/output.
- Real topology lấy từ DB management: `area`, `areaCrosses`, `crosses`, `roads`, `cycles`, `stages`.
- `simToReal` không có sẵn trong DB management. Đây là mapping overlay sim/training ID -> real DB cross ID, phải được operator/integration team confirm.
- Snapshot sau khi sync sẽ được compile vào `models/real_normalization/area_<area_id>/`; runtime dùng dữ liệu này để hydrate `cycleLength`, `yellow/redClear`, road static và direction.
- Inference production nên dùng compact payload: chỉ gửi trạng thái đèn hiện tại và nhu cầu giao thông, không gửi lại topology mỗi chu kỳ.
- Production không activate runtime bundle nếu `compatibility_report.json` có warning `AUTO_CROSS_MAPPING_BY_ORDER`.
- Production nên đặt `SIM_BUNDLE_AUTO_ACTIVATE=false` để review compatibility report trước khi go-live.

## Reading paths

### Integrator / Core Controller

```text
core-controller-api-contract.md
  -> ../api_docs/run_ai_algorithm.md
  -> integration-real-controller.md
  -> troubleshooting.md
```

### DevOps / SRE

```text
deployment.md
  -> configuration.md
  -> auto-sync.md
  -> troubleshooting.md
```

### Developer

```text
PIPELINE.md
  -> architecture.md
  -> end-to-end-test.md
  -> testing.md
```

## Repo-level docs

- [../README.md](../README.md): overview nhanh của service.
- [../postman/README.md](../postman/README.md): cách import và chạy Postman collection.
- [../api_docs/run_ai_algorithm.md](../api_docs/run_ai_algorithm.md): schema riêng cho endpoint inference chính.
