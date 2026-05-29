"""Test guardrails — Lop 4 Safety Layer."""
from __future__ import annotations

import pytest

from src.runtime.guardrails import apply_guardrails
from src.runtime.starvation import StarvationTracker


def _base_kwargs(**override):
    """Base kwargs cho 1 cross 2 stage. Override bang kwargs."""
    base = dict(
        cross_id=1,
        proposed_green_times=[30, 30],
        current_green_times=[30, 30],
        yellow_times=[3, 3],
        red_clear_times=[1, 1],
        cycle_length=90,
        masked_stage_indices=[],
        tracker=StarvationTracker(),
    )
    base.update(override)
    return base


def test_min_green_clip():
    """proposed=5 < min_green=10 -> clip len 10."""
    report = apply_guardrails(
        **_base_kwargs(proposed_green_times=[5, 30])
    )
    assert report.triggered
    assert report.decisions[0].green_time == 10
    rules = [v.rule for v in report.violations]
    assert "min_green" in rules


def test_max_green_clip():
    """proposed=120 > max_green=60 -> clip xuong 60."""
    report = apply_guardrails(
        **_base_kwargs(proposed_green_times=[120, 30])
    )
    assert report.triggered
    assert report.decisions[0].green_time == 60
    rules = [v.rule for v in report.violations]
    assert "max_green" in rules


def test_no_violation_when_in_range():
    report = apply_guardrails(
        **_base_kwargs(proposed_green_times=[30, 30])
    )
    assert not report.triggered
    assert report.decisions[0].green_time == 30
    assert report.decisions[1].green_time == 30


def test_masked_stage_force_keep():
    """Stage masked -> giu nguyen current, khong cho dieu chinh."""
    report = apply_guardrails(
        **_base_kwargs(
            proposed_green_times=[50, 30],  # muon doi stage 0
            current_green_times=[30, 30],
            masked_stage_indices=[0],
        )
    )
    assert report.decisions[0].green_time == 30  # giu current
    assert report.decisions[0].masked is True
    rules = [v.rule for v in report.violations]
    assert "mask_keep" in rules


def test_anti_starvation_bumps_after_max_skips():
    """Goi nhieu lan voi green=min_green -> sau N lan, bump len recovery_green=15."""
    tracker = StarvationTracker()
    cross_id = 99
    stage_idx = 0

    # 3 lan dau (max_skips=3): khong bump.
    for _ in range(3):
        report = apply_guardrails(
            **_base_kwargs(
                cross_id=cross_id,
                proposed_green_times=[10, 30],
                current_green_times=[10, 30],
                tracker=tracker,
            )
        )
        assert report.decisions[0].green_time == 10

    # Lan thu 4 (count=4 > 3): bump.
    report = apply_guardrails(
        **_base_kwargs(
            cross_id=cross_id,
            proposed_green_times=[10, 30],
            current_green_times=[10, 30],
            tracker=tracker,
        )
    )
    assert report.decisions[0].green_time == 15
    rules = [v.rule for v in report.violations]
    assert "anti_starvation" in rules


def test_anti_starvation_resets_when_above_min():
    """Khi green > min_green, counter reset -> khong bump tu nho ban dau."""
    tracker = StarvationTracker()
    cross_id = 7
    # 3 lan tai min
    for _ in range(3):
        apply_guardrails(**_base_kwargs(
            cross_id=cross_id,
            proposed_green_times=[10, 30],
            current_green_times=[10, 30],
            tracker=tracker,
        ))
    # Sau do 1 lan above min -> counter reset
    apply_guardrails(**_base_kwargs(
        cross_id=cross_id,
        proposed_green_times=[30, 30],
        current_green_times=[30, 30],
        tracker=tracker,
    ))
    # Giay lai ve min: count moi se = 1, chua trigger anti-starvation.
    report = apply_guardrails(**_base_kwargs(
        cross_id=cross_id,
        proposed_green_times=[10, 30],
        current_green_times=[10, 30],
        tracker=tracker,
    ))
    assert report.decisions[0].green_time == 10
    rules = [v.rule for v in report.violations]
    assert "anti_starvation" not in rules


def test_empty_proposed_returns_empty_report():
    report = apply_guardrails(
        cross_id=1,
        proposed_green_times=[],
        current_green_times=[],
        yellow_times=[],
        red_clear_times=[],
        cycle_length=60,
    )
    assert report.decisions == []
    assert not report.triggered


def test_exceeds_cycle_clipped():
    """Green > available_total (= cycle - sum(yellow+red_clear)) -> clip."""
    # cycle=30, yellow=[3,3], red_clear=[1,1] -> total_fixed=8 -> available=22
    # proposed[0]=50 -> clip xuong min(max_g=60, 22) = 22 (sau khi clip max=60)
    report = apply_guardrails(
        **_base_kwargs(
            cycle_length=30,
            proposed_green_times=[50, 10],
            current_green_times=[20, 10],
        )
    )
    rules = [v.rule for v in report.violations]
    # max_green clip xay ra TRUOC exceeds_cycle clip
    assert "max_green" in rules or "exceeds_cycle" in rules
    assert report.decisions[0].green_time <= 22
