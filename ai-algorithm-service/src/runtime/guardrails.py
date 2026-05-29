"""Guardrails (Safety Layer) — buoc cuoi cung trong Inference Pipeline.

Lop bao ve cuoi tai ai-runtime, kiem tra moi pha den sau khi Phase Normalizer
da tra ve. Bat ke ONNX co "hallucinate" the nao, lenh ra den van phai an toan.

Rule:
  1. Min/Max green clip: cap [min_green, max_green] cho moi stage.
  2. Anti-starvation: stage bi pin tai min_green qua N lan lien tiep -> bump
     len recovery_green.
  3. Traffic rule basic: green_time + yellow + red_clear phai > 0; tong khong
     vuot cycle_length.
  4. Khong cho stage bi mask (-1) thay doi green-time so voi current.

Tra ve `GuardrailReport` voi list violation va danh sach stage da bi sua.
Guardrails KHONG nem exception — luon tra ket qua an toan, dong thoi log de
caller co the audit `guardrail_triggered=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from src.core.config import get_settings
from src.runtime.starvation import StarvationTracker, get_starvation_tracker


@dataclass
class GuardrailViolation:
    cross_id: int
    stage_idx: int
    rule: str
    detail: str
    original_value: Optional[float] = None
    adjusted_value: Optional[float] = None


@dataclass
class GuardrailDecision:
    """Quyet dinh dau ra cho 1 stage sau khi qua Guardrails."""
    stage_idx: int
    green_time: int
    original_green_time: int
    masked: bool


@dataclass
class GuardrailReport:
    cross_id: int
    decisions: List[GuardrailDecision] = field(default_factory=list)
    violations: List[GuardrailViolation] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.violations)


def apply_guardrails(
    *,
    cross_id: int,
    proposed_green_times: Sequence[float],
    current_green_times: Sequence[int],
    yellow_times: Sequence[int],
    red_clear_times: Sequence[int],
    cycle_length: int,
    masked_stage_indices: Optional[Sequence[int]] = None,
    tracker: Optional[StarvationTracker] = None,
) -> GuardrailReport:
    """Ap dung guardrails cho 1 cross.

    Tat ca array cung do dai = num_stages. Tra `GuardrailReport`.
    """
    settings = get_settings()
    tracker = tracker or get_starvation_tracker()
    report = GuardrailReport(cross_id=cross_id)

    masked = set(masked_stage_indices or [])
    n = len(proposed_green_times)
    if n == 0:
        return report

    min_g = settings.guardrail_min_green
    max_g = settings.guardrail_max_green
    starv_max = settings.guardrail_anti_starvation_max_skips
    recover_g = settings.guardrail_anti_starvation_recovery_green

    enabled = settings.guardrail_enabled

    total_fixed = sum(yellow_times[i] + red_clear_times[i] for i in range(n))
    available_total = max(0, cycle_length - total_fixed)

    for i in range(n):
        original = float(proposed_green_times[i])
        green = original
        violations_for_stage: List[GuardrailViolation] = []

        if i in masked:
            # Stage bi mask -> giu nguyen current green-time, khong cho dieu chinh.
            current = int(current_green_times[i])
            if int(round(green)) != current:
                violations_for_stage.append(GuardrailViolation(
                    cross_id=cross_id, stage_idx=i, rule="mask_keep",
                    detail=f"Stage masked, force keep current={current}",
                    original_value=original, adjusted_value=float(current),
                ))
                green = float(current)
            decision = GuardrailDecision(
                stage_idx=i, green_time=int(round(green)),
                original_green_time=int(round(original)), masked=True,
            )
            report.decisions.append(decision)
            report.violations.extend(violations_for_stage)
            continue

        if not enabled:
            report.decisions.append(GuardrailDecision(
                stage_idx=i, green_time=int(round(green)),
                original_green_time=int(round(original)), masked=False,
            ))
            continue

        # Rule 1: min/max clip
        if green < min_g:
            violations_for_stage.append(GuardrailViolation(
                cross_id=cross_id, stage_idx=i, rule="min_green",
                detail=f"clip {green:.1f} -> {min_g}",
                original_value=original, adjusted_value=float(min_g),
            ))
            green = float(min_g)
        elif green > max_g:
            violations_for_stage.append(GuardrailViolation(
                cross_id=cross_id, stage_idx=i, rule="max_green",
                detail=f"clip {green:.1f} -> {max_g}",
                original_value=original, adjusted_value=float(max_g),
            ))
            green = float(max_g)

        # Rule 2: anti-starvation
        if int(round(green)) <= min_g:
            count = tracker.record_min_green(cross_id, i)
            if count > starv_max:
                bumped = max(min_g, recover_g)
                if bumped > green:
                    violations_for_stage.append(GuardrailViolation(
                        cross_id=cross_id, stage_idx=i, rule="anti_starvation",
                        detail=f"{count} lan lien tiep tai min, bump -> {bumped}",
                        original_value=original, adjusted_value=float(bumped),
                    ))
                    green = float(bumped)
                tracker.reset(cross_id, i)
        else:
            tracker.reset(cross_id, i)

        # Rule 3: traffic rule basic
        if available_total > 0 and green > available_total:
            violations_for_stage.append(GuardrailViolation(
                cross_id=cross_id, stage_idx=i, rule="exceeds_cycle",
                detail=f"green {green:.1f} > available {available_total}",
                original_value=original, adjusted_value=float(available_total),
            ))
            green = float(available_total)

        decision = GuardrailDecision(
            stage_idx=i, green_time=int(round(green)),
            original_green_time=int(round(original)), masked=False,
        )
        report.decisions.append(decision)
        report.violations.extend(violations_for_stage)

    return report
