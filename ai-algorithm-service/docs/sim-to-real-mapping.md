# Sim → Real Mapping

Tài liệu này giải thích mapping giữa ID phía SUMO và ID phía DB thật trong pipeline hiện tại. Luồng chạy chi tiết nằm ở [sim-to-real-pipeline.md](sim-to-real-pipeline.md) và [end-to-end-test.md](end-to-end-test.md).

---

## 1. Ba Không Gian ID

```text
SUMO / Training                 Runtime Standard               Real DB
----------------                ----------------               ----------------
sim_tls_id                      std_phase_idx 0..7             real_cross_id
sim_edge_id                     direction_idx 0..3             real_road_id
sim_phase_idx                   action_idx                     real_cycle_id
sim_network.json                feature channels               real_stage_id
```

Policy chỉ hiểu standard/action/feature space. Vì vậy runtime bundle phải khóa mapping từ sim sang real trước khi inference.

`direction_idx` (0=N, 1=E, 2=S, 3=W) **không lấy thẳng từ cột DB**, mà được suy lại trong [real_normalization.py](../src/ops/real_normalization.py) bằng cách:
- Ưu tiên dùng GPS (cross center + road polyline) → thuật toán GPI giống Service-ai → khử mọi ambiguity về encoding 4-dir vs 8-dir.
- Fallback legacy `from_cross_direction` / `to_cross_direction` với encoding tự auto-detect.

Chi tiết: [PIPELINE.md §4.6](PIPELINE.md#46-direction-inference-gps-first-legacy-fallback).

---

## 2. Artifact Mapping

| Artifact | Nguồn | Chứa gì |
|---|---|---|
| `sim_network.json` | Training team | SUMO TLS IDs, edge IDs, lane/direction/phases |
| `real_normalization.json` | AI service compile từ `real_network_snapshot` | real cross/road/cycle/stage IDs, roads_static |
| `deployment_map.json` | AI service generate khi compose | Bridge sim IDs → real IDs |
| `intersections/cross_<real_id>.json` | Runtime bundle | Config runtime keyed bằng real DB IDs |

---

## 3. Cách Composer Map

Trong [src/ops/composer.py](../src/ops/composer.py), composer tạo `deployment_map.json` theo thứ tự ưu tiên:

1. Nếu `real_normalization.json` có `sim_to_real`, `cross_map`, hoặc `sim_to_real_crosses`, dùng mapping explicit.
2. Nếu từng real cross có `sim_tls_id` hoặc `sim_cross_id`, dùng field đó.
3. Nếu không có mapping explicit nhưng số cross sim và real bằng nhau, map theo thứ tự và ghi warning `AUTO_CROSS_MAPPING_BY_ORDER`.
4. Nếu không map được, compose fail.

Production nên gửi `simToReal` trong real network snapshot để tránh map sai cross khi dữ liệu DB reorder.

---

## 4. Validation Compatibility

Composer validate trước khi build runtime bundle:

| Check | Fail khi |
|---|---|
| Cross coverage | Sim cross thiếu real mapping hoặc real mapping trỏ sai |
| Direction mapping | Sim có edge ở hướng N/E/S/W nhưng real thiếu road tương ứng — đồng nghĩa cả GPS lẫn legacy code đều không xác định được hướng đó (xem `_classify_road_at_cross`) |
| Phase/stage count | Số sim phases khác số real stages trong primary cycle |
| Standard phase | `std_phase_idx` sinh ra không khớp `actual_to_standard` trong sim network |
| Bundle checksum | Runtime bundle bị sửa sau khi build |

Kết quả được ghi vào `compatibility_report.json` và nhúng vào final runtime bundle.

Khi `Direction mapping` fail, không có round-robin fallback — composer dừng ngay. Đây là thiết kế chủ động: feed sai channel vào policy đã train trên 4 hướng N/E/S/W sẽ ra inference output không xác định, an toàn hơn khi fail loud.

---

## 5. Runtime Không Dùng Sim ID

Sau khi activate, runtime inference chỉ dùng real DB IDs:

```text
controller payload cross.id / road.id / cycle.id / stage.id
        ↓
intersections/cross_<real_id>.json
        ↓
FeatureBuilder + PhaseNormalizer
        ↓
ONNX policy
        ↓
response stageId real DB
```

`sim_network.json` chỉ còn vai trò audit/debug trong runtime bundle.
