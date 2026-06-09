from __future__ import annotations

import numpy as np
import pytest

from src.core.exception import AlgorithmException
from src.preprocessing.intersection_registry import IntersectionConfig
from src.schemas.ai_schemas.ai_input import AIInput
from src.schemas.common_schemas.cross import Cross
from src.schemas.common_schemas.cycle import Cycle
from src.schemas.common_schemas.road import Road
from src.schemas.common_schemas.stage_input import StageInput
from src.services.ai_service import AIService


def _make_cross(
    *,
    cycle_length: int = 90,
    red_clears: tuple[int, ...] = (1, 1),
    stage_duration: int = 45,
) -> Cross:
    stages = [
        StageInput(
            id=idx + 1,
            stageCode=f"P{idx}",
            oldId=f"p{idx}",
            yellow=3,
            redClear=red_clear,
            duration=stage_duration,
        )
        for idx, red_clear in enumerate(red_clears)
    ]
    return Cross(
        id=1001,
        areaId=1,
        cycle=Cycle(
            id=1,
            createdDate="2026-01-01",
            crossName="Cycle Balance Test",
            cycleLength=cycle_length,
        ),
        stages=stages,
        roads=[
            Road(
                id=1,
                direction=1,
                saturationFlow=1800,
                averageSpeed=10,
                occupancySpace=20,
            )
        ],
    )


def _run_plan(cross: Cross, *, min_green: int = 15, max_green: int = 60):
    service = AIService(
        AIInput(
            crosses=[cross],
            yellowTime=3,
            minGreen=min_green,
            maxGreen=max_green,
            greenTimeStep=5,
        )
    )
    output, _ = service._actions_to_signal_plan(
        cross=cross,
        actions_standard=np.ones(8, dtype=np.int64),
        config=None,
        area_id=cross.areaId,
        num_actions=3,
        keep_idx=1,
    )
    return output


def _duration_sum(output) -> int:
    return sum(
        p.greenTime + p.yellowTime + p.redClearTime
        for p in output.phases
    )


def test_signal_plan_balances_cycle_with_all_red():
    output = _run_plan(_make_cross(cycle_length=90, red_clears=(1, 1)))

    assert _duration_sum(output) == 90
    assert sum(p.greenTime for p in output.phases) == 82


def test_signal_plan_output_is_compact_command():
    output = _run_plan(_make_cross(cycle_length=90, red_clears=(1, 1)))
    data = output.model_dump()

    assert set(data) == {"crossId", "cycleId", "cycleLength", "phases"}
    assert set(data["phases"][0]) == {
        "stageId",
        "greenTime",
        "yellowTime",
        "redClearTime",
    }


def test_signal_plan_balances_cycle_without_all_red():
    output = _run_plan(_make_cross(cycle_length=90, red_clears=(0, 0)))

    assert _duration_sum(output) == 90
    assert sum(p.greenTime for p in output.phases) == 84


def test_signal_plan_balances_cycle_with_mixed_all_red():
    output = _run_plan(_make_cross(cycle_length=90, red_clears=(1, 0, 2)))

    assert _duration_sum(output) == 90
    assert sum(p.greenTime for p in output.phases) == 78


def test_signal_plan_rejects_cycle_shorter_than_min_green_and_fixed_time():
    cross = _make_cross(cycle_length=20, red_clears=(1, 1), stage_duration=10)

    with pytest.raises(AlgorithmException):
        _run_plan(cross, min_green=15)


def test_hydrates_compact_runtime_payload_from_intersection_config(monkeypatch):
    cfg = IntersectionConfig(
        cross_id=1001,
        primary_cycle_id=10,
        cycles={
            "10": {
                "cycle_length": 90,
                "cycle_name": "Hydrated cycle",
                "stage_to_standard_phase": {"1": 0, "2": 1},
                "standard_phase_to_stage": {"0": 1, "1": 2},
                "stages": [
                    {
                        "id": 1,
                        "stage_code": "S1",
                        "old_id": "old-1",
                        "yellow": 3,
                        "red_clear": 1,
                    },
                    {
                        "id": 2,
                        "stage_code": "S2",
                        "old_id": "old-2",
                        "yellow": 3,
                        "red_clear": 0,
                    },
                ],
            }
        },
        roads_static={
            "501": {
                "lanes": 1,
                "length_meters": 100,
                "speed_design_kmh": 50,
                "saturation_flow": 1800,
            }
        },
    )
    monkeypatch.setattr("src.services.ai_service.get_config", lambda area_id, cross_id: cfg)

    ai_input = AIInput(
        areaId=1,
        crosses=[
            {
                "crossId": 1001,
                "cycleId": 10,
                "stages": [
                    {"stageId": 1, "greenTime": 41},
                    {"stageId": 2, "greenTime": 42},
                ],
                "roads": [
                    {
                        "roadId": 501,
                        "averageSpeed": 30,
                        "averageSpeedUnit": "km/h",
                        "occupancySpace": 40,
                    }
                ],
            }
        ],
    )

    hydrated = AIService(ai_input)._hydrate_runtime_crosses(ai_input)

    assert hydrated[0].areaId == 1
    assert hydrated[0].cycle.cycleLength == 90
    assert hydrated[0].stages[0].duration == 45
    assert hydrated[0].stages[0].stageCode == "S1"
    assert hydrated[0].stages[1].redClear == 0
    assert hydrated[0].roads[0].saturationFlow == 1800
