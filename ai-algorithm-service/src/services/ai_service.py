"""
AI Service: inference orchestration cho MGMQ-PPO (ONNX runtime).

Pipeline một request:
 1. Group cross theo areaId.
 2. Với mỗi nhóm:
    a. Đảm bảo topology config cho area (auto-generate lần đầu + lưu disk).
    b. Dựng observation + action_mask cho từng cross (48 feature lane + 8 feature
       green-time ratio, sau đó z-score bằng obs_stats của policy).
    c. Pack self_features + neighbor_features (per-agent K=max_neighbors neighbor)
       cho Local-GNN policy, hoặc raw obs cho Global-GNN policy.
    d. Chạy ONNX session 1 lần cho cả nhóm (batch = số cross của area).
 3. Map 8 standard actions -> thời gian đèn xanh theo từng stage thực, rescale
    giữ nguyên tổng green.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.core.config import get_settings
from src.core.error_codes import ErrorCode
from src.core.exception import AlgorithmException
from src.core.logger import logger
from src.preprocessing import (
    FeatureNormalizer,
    MAX_NEIGHBORS,
    build_action_mask,
    build_lane_features,
    ensure_area_configs,
    extract_green_time_ratios,
    get_config,
    get_neighbor_ids,
    get_observation_history,
    map_stage_actions,
)
from src.preprocessing.feature_builder import build_from_bundle, get_default_builder
from src.preprocessing.intersection_registry import bundle_root_for_area
from src.preprocessing.phase_normalizer import NUM_STANDARD_PHASES
from src.preprocessing.topology_normalizer import TOTAL_LANES
from src.observability.metrics import (
    record_guardrail_violation,
    record_inference_metric,
)
from src.observability import drift_registry
from src.runtime.guardrails import GuardrailReport, apply_guardrails
from src.schemas.ai_schemas.ai_input import AIInput
from src.schemas.ai_schemas.ai_output import AIOutput
from src.schemas.ai_schemas.algorithm_output import AlgorithmOutput
from src.schemas.common_schemas.cross import Cross
from src.schemas.common_schemas.stage_output import StageOutput
from src.services import audit_service
from src.services.model_manager import AreaPolicy, load_policy
from src.services.readiness_service import check_area


LANE_FEATURE_DIM = TOTAL_LANES * 4  # 48


class AIService:
    def __init__(self, ai_input: AIInput):
        self.yellow_time = ai_input.yellowTime
        self.min_green = ai_input.minGreen
        self.max_green = ai_input.maxGreen
        self.green_time_step = ai_input.greenTimeStep

    def run(self, ai_input: AIInput, *, request_id: str = "") -> AIOutput:
        crosses = ai_input.crosses
        if not crosses:
            raise AlgorithmException(
                "Danh sach cross khong duoc rong.",
                code=ErrorCode.INVALID_INPUT,
            )

        # Group by areaId, giu thu tu goc trong request de output align.
        groups: Dict[int, List[Tuple[int, Cross]]] = defaultdict(list)
        for idx, c in enumerate(crosses):
            groups[c.areaId].append((idx, c))

        settings = get_settings()
        if settings.enforce_single_area_per_request and len(groups) > 1:
            raise AlgorithmException(
                (
                    f"Request chua {len(groups)} area ({sorted(groups)}). "
                    f"Contract yeu cau 1 area/request."
                ),
                code=ErrorCode.MULTIPLE_AREAS_NOT_ALLOWED,
            )

        # Readiness guard: moi area phai ready truoc khi inference.
        for area_id in groups:
            check = check_area(area_id)
            if not check.ready:
                raise AlgorithmException(
                    f"Area {area_id} chua san sang: missing={check.missing}.",
                    code=ErrorCode.AREA_NOT_READY,
                    area_id=area_id,
                    extra={"missing": check.missing},
                )

        outputs: List[Optional[AlgorithmOutput]] = [None] * len(crosses)
        t0 = time.perf_counter()
        policy_version: Optional[str] = None
        config_version: Optional[str] = None
        bundle_id: Optional[str] = None
        guardrail_triggered = False
        first_area_id: Optional[int] = next(iter(groups), None)

        try:
            for area_id, items in groups.items():
                area_crosses = [c for _, c in items]
                area_outputs, area_triggered = self._run_area(area_id, area_crosses)
                if area_triggered:
                    guardrail_triggered = True
                for (orig_idx, _), out in zip(items, area_outputs):
                    outputs[orig_idx] = out

                # Lay version + bundle_id tu policy de them vao audit.
                pol = load_policy(area_id)
                policy_version = pol.policy_version or pol.meta.get("policy_version") or policy_version
                config_version = pol.config_version or pol.meta.get("config_version") or config_version
                bundle_id = pol.bundle_id or bundle_id
        except AlgorithmException as exc:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            audit_service.record_inference(
                request_id=request_id,
                area_id=exc.area_id or first_area_id,
                policy_version=policy_version,
                config_version=config_version,
                bundle_id=bundle_id,
                guardrail_triggered=guardrail_triggered,
                num_crosses=len(crosses),
                latency_ms=latency_ms,
                status="error",
                error_code=exc.code.value,
            )
            record_inference_metric(role="runtime", status="error", latency_ms=latency_ms)
            raise
        except Exception:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            audit_service.record_inference(
                request_id=request_id,
                area_id=first_area_id,
                policy_version=policy_version,
                config_version=config_version,
                bundle_id=bundle_id,
                guardrail_triggered=guardrail_triggered,
                num_crosses=len(crosses),
                latency_ms=latency_ms,
                status="error",
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
            record_inference_metric(role="runtime", status="error", latency_ms=latency_ms)
            raise

        latency_ms = int((time.perf_counter() - t0) * 1000)
        audit_service.record_inference(
            request_id=request_id,
            area_id=first_area_id,
            policy_version=policy_version,
            config_version=config_version,
            bundle_id=bundle_id,
            guardrail_triggered=guardrail_triggered,
            num_crosses=len(crosses),
            latency_ms=latency_ms,
            status="ok",
        )
        record_inference_metric(role="runtime", status="ok", latency_ms=latency_ms)

        return AIOutput(
            status=1,
            numIntersections=len(crosses),
            areaIds=sorted(groups.keys()),
            algorithmOutputs=[o for o in outputs if o is not None],
        )

    # ------------------------------------------------------------------
    # Per-area pipeline
    # ------------------------------------------------------------------

    def _run_area(
        self, area_id: int, crosses: List[Cross]
    ) -> Tuple[List[AlgorithmOutput], bool]:
        policy = load_policy(area_id)
        network = ensure_area_configs(area_id, crosses)

        # Doc runtime contract da validate o load_policy.
        obs_dim = int(policy.meta["obs_dim"])
        base_obs_dim = int(policy.meta["base_obs_dim"])
        window_size = int(policy.meta["window_size"])
        num_actions = int(policy.meta["num_actions_per_phase"])
        keep_idx = int(policy.meta["keep_action_index"])

        include_gt_ratios = base_obs_dim >= LANE_FEATURE_DIM + NUM_STANDARD_PHASES
        history = get_observation_history()

        # Per-cross observation (full window) + mask + config
        obs_by_id: Dict[int, np.ndarray] = {}
        mask_by_id: Dict[int, np.ndarray] = {}
        cfgs: Dict[int, object] = {}

        # Load FeatureBuilder cho area (compile formula 1 lan + cache). Bundle
        # v2 -> dung feature_formula.json; v1 hoac chua co bundle -> default.
        bundle_root = bundle_root_for_area(area_id)
        if bundle_root is not None:
            cross_configs_dict: Dict[int, dict] = {}
            for c in crosses:
                cfg_tmp = get_config(area_id, c.id)
                if cfg_tmp is not None:
                    cross_configs_dict[c.id] = cfg_tmp.to_dict()
            feature_builder = build_from_bundle(
                bundle_root=bundle_root,
                cross_configs=cross_configs_dict,
                cache_key=(area_id, policy.bundle_id),
            )
        else:
            feature_builder = get_default_builder()

        for c in crosses:
            cfg = get_config(area_id, c.id)
            cfgs[c.id] = cfg

            # Build observation tai timestep hien tai, shape (base_obs_dim,).
            lane_features, _ = build_lane_features(c, cfg, feature_builder=feature_builder)
            obs_t = lane_features.flatten().astype(np.float32)
            if include_gt_ratios:
                obs_t = np.concatenate([obs_t, extract_green_time_ratios(c)]).astype(np.float32)
            if obs_t.shape[0] < base_obs_dim:
                obs_t = np.concatenate(
                    [obs_t, np.zeros(base_obs_dim - obs_t.shape[0], dtype=np.float32)]
                )
            elif obs_t.shape[0] > base_obs_dim:
                obs_t = obs_t[:base_obs_dim]

            # Push qua history buffer, lay full window. window_size=1 -> equivalent
            # behavior nhung van di qua buffer (vo hai).
            window = history.push_and_get_window(
                area_id=area_id,
                cross_id=c.id,
                obs_t=obs_t,
                window_size=window_size,
                base_obs_dim=base_obs_dim,
            )
            obs_full = window.flatten()  # (window_size * base_obs_dim,) = (obs_dim,)

            normalizer = FeatureNormalizer(mean=policy.obs_mean, std=policy.obs_std)
            obs_by_id[c.id] = normalizer.apply(obs_full)
            mask_by_id[c.id] = build_action_mask(c, cfg)

        # Run ONNX
        use_local = bool(policy.meta.get("use_local_gnn", True))
        if use_local:
            actions = self._run_local_gnn(
                policy, crosses, network, obs_by_id, mask_by_id,
                obs_dim, base_obs_dim, window_size, num_actions, keep_idx,
            )
        else:
            actions = self._run_global(
                policy, crosses, obs_by_id, mask_by_id, num_actions, keep_idx,
            )

        # Drift detection: observe `obs_mean` (trung binh observation da z-scored).
        # Goi best-effort, khong chan inference flow neu drift module loi.
        try:
            net_id = policy.network_id or f"area_{area_id}"
            for c in crosses:
                obs_mean_val = float(obs_by_id[c.id].mean())
                drift_registry.record_observation(
                    network_id=net_id,
                    feature="obs_mean",
                    value=obs_mean_val,
                    bundle_id=policy.bundle_id,
                )
            drift_registry.maybe_check(net_id)
        except Exception as e:
            logger.warning(f"[drift] observe/check failed: {e}")

        logger.info(f"Area {area_id}: inference xong cho {len(crosses)} cross")

        outputs: List[AlgorithmOutput] = []
        any_triggered = False
        for i, c in enumerate(crosses):
            out, triggered = self._actions_to_signal_plan(
                c, actions[i], cfgs[c.id], area_id, num_actions, keep_idx,
            )
            outputs.append(out)
            if triggered:
                any_triggered = True
        return outputs, any_triggered

    def _run_local_gnn(
        self,
        policy: AreaPolicy,
        crosses: List[Cross],
        network: dict,
        obs_by_id: Dict[int, np.ndarray],
        mask_by_id: Dict[int, np.ndarray],
        obs_dim: int,
        base_obs_dim: int,
        window_size: int,
        num_actions: int,
        keep_idx: int,
    ) -> np.ndarray:
        K = int(policy.meta.get("max_neighbors", MAX_NEIGHBORS))
        B = len(crosses)

        self_feat = np.zeros((B, obs_dim), dtype=np.float32)
        neighbor_feat = np.zeros((B, K, obs_dim), dtype=np.float32)
        neighbor_mask = np.zeros((B, K), dtype=np.float32)
        neighbor_dirs = np.zeros((B, K), dtype=np.float32)
        action_mask = np.zeros((B, NUM_STANDARD_PHASES), dtype=np.float32)

        for i, c in enumerate(crosses):
            self_feat[i] = obs_by_id[c.id]
            action_mask[i] = mask_by_id[c.id]
            for k, nbr in enumerate(get_neighbor_ids(network, c.id)[:K]):
                nid = int(nbr["neighbor_id"])
                if nid in obs_by_id:
                    neighbor_feat[i, k] = obs_by_id[nid]
                    neighbor_mask[i, k] = 1.0
                    neighbor_dirs[i, k] = int(nbr.get("direction", 0))

        feeds = {
            "self_features": self_feat,
            "neighbor_features": neighbor_feat,
            "neighbor_mask": neighbor_mask,
            "neighbor_directions": neighbor_dirs,
            "action_mask": action_mask,
        }

        if window_size > 1:
            feeds["self_features"] = feeds["self_features"].reshape(B, window_size, base_obs_dim)
            feeds["neighbor_features"] = feeds["neighbor_features"].reshape(B, K, window_size, base_obs_dim)
        # Filter theo input_names cua session (model co the bo qua neighbor_directions
        # hoac action_mask tuy variant).
        feeds = {k: v for k, v in feeds.items() if k in policy.input_names}

        logits = policy.session.run([policy.output_name], feeds)[0]
        return self._logits_to_actions(logits, action_mask, num_actions, keep_idx)

    def _run_global(
        self,
        policy: AreaPolicy,
        crosses: List[Cross],
        obs_by_id: Dict[int, np.ndarray],
        mask_by_id: Dict[int, np.ndarray],
        num_actions: int,
        keep_idx: int,
    ) -> np.ndarray:
        obs_batch = np.stack([obs_by_id[c.id] for c in crosses], axis=0).astype(np.float32)
        action_mask = np.stack([mask_by_id[c.id] for c in crosses], axis=0).astype(np.float32)

        feed_name = policy.input_names[0]
        logits = policy.session.run([policy.output_name], {feed_name: obs_batch})[0]
        return self._logits_to_actions(logits, action_mask, num_actions, keep_idx)

    @staticmethod
    def _logits_to_actions(
        logits: np.ndarray,
        action_mask: np.ndarray,
        num_actions: int,
        keep_idx: int,
    ) -> np.ndarray:
        """
        logits: [B, 8*A] hoac [B, 8, A]; action_mask: [B, 8].
        Tra ve actions shape [B, 8] voi gia tri ∈ [0, A). Phase bi mask -> keep_idx
        (action giu nguyen thoi luong, theo dinh nghia action space training).
        """
        B = logits.shape[0]
        if logits.ndim == 2:
            logits = logits.reshape(B, NUM_STANDARD_PHASES, num_actions)
        elif logits.shape[-1] != num_actions:
            raise ValueError(
                f"Logits last dim={logits.shape[-1]} != num_actions={num_actions}. "
                f"Bundle khai bao sai num_actions_per_phase hoac ONNX khong khop."
            )

        keep = np.full_like(logits, -1e9)
        keep[..., keep_idx] = 0.0
        mask = action_mask[..., None]  # [B,8,1]
        logits = np.where(mask > 0, logits, keep)
        return logits.argmax(axis=-1).astype(np.int64)  # [B, 8]

    # ------------------------------------------------------------------
    # Post-processing (giữ logic cũ)
    # ------------------------------------------------------------------

    def _actions_to_signal_plan(
        self,
        cross: Cross,
        actions_standard: np.ndarray,
        config,
        area_id: int,
        num_actions: int,
        keep_idx: int,
    ) -> Tuple[AlgorithmOutput, bool]:
        stage_actions = map_stage_actions(
            actions_standard, cross, config, keep_action_index=keep_idx,
        )
        num_stages = len(cross.stages)

        total_yellow_time = self.yellow_time * num_stages
        total_green_time = int(cross.cycle.cycleLength) - total_yellow_time

        current_green_times = [
            max(self.min_green, stage.duration - stage.yellow - stage.redClear)
            for stage in cross.stages
        ]

        # Action -> green-time delta: dich tu keep_idx, moi step = green_time_step
        # giay. Vi du num_actions=5, keep_idx=2 -> [-2s,-1s,0,+1s,+2s] * step.
        # num_actions=3, keep_idx=1 -> [-step, 0, +step]. Khop voi action space
        # da train.
        def _action_to_delta(action_idx: int) -> int:
            a = int(action_idx)
            if not 0 <= a < num_actions:
                return 0
            return (a - keep_idx) * self.green_time_step

        new_green_times: List[float] = []
        for i, current_g in enumerate(current_green_times):
            action = stage_actions[i] if i < len(stage_actions) else keep_idx
            adj = _action_to_delta(action)
            new_g = max(self.min_green, min(self.max_green, current_g + adj))
            new_green_times.append(new_g)

        new_green_times_arr = self._rescale_green_times(
            np.array(new_green_times, dtype=float),
            total_green_time,
        )

        # Guardrails (Lop 4 — Safety Layer)
        masked_indices: List[int] = []
        for i in range(num_stages):
            if i >= len(stage_actions):
                masked_indices.append(i)
                continue
            # phase_normalizer.map_stage_actions tra "1" cho stage bi mask hoac
            # khong map duoc. Khong de phan biet that su -> rely on config.
            if config is not None and config.phase_mapping is not None:
                if i < len(config.phase_mapping):
                    if int(config.phase_mapping[i]) < 0:
                        masked_indices.append(i)

        report: GuardrailReport = apply_guardrails(
            cross_id=cross.id,
            proposed_green_times=new_green_times_arr.tolist(),
            current_green_times=current_green_times,
            yellow_times=[s.yellow for s in cross.stages],
            red_clear_times=[s.redClear for s in cross.stages],
            cycle_length=int(cross.cycle.cycleLength),
            masked_stage_indices=masked_indices,
        )
        if report.triggered:
            for v in report.violations:
                logger.warning(
                    f"[guardrail] cross={v.cross_id} stage={v.stage_idx} "
                    f"rule={v.rule} {v.detail}"
                )
                record_guardrail_violation(v.rule)

        green_by_idx = {d.stage_idx: d.green_time for d in report.decisions}

        output_stages: List[StageOutput] = []
        for idx, stage in enumerate(cross.stages):
            if idx in green_by_idx:
                green_time = green_by_idx[idx]
            elif idx < len(new_green_times_arr):
                green_time = int(new_green_times_arr[idx])
            else:
                green_time = self.min_green
            output_stages.append(StageOutput(
                stageId=stage.id,
                stageCode=stage.stageCode,
                oldId=stage.oldId,
                greenTime=max(1, int(green_time)),
                yellowTime=stage.yellow,
                redClearTime=stage.redClear,
            ))

        return (
            AlgorithmOutput(
                cycleLength=int(cross.cycle.cycleLength),
                crossId=cross.id,
                areaId=area_id,
                crossName=getattr(cross.cycle, "crossName", None),
                cycleId=cross.cycle.id if hasattr(cross.cycle, "id") else None,
                createdDate=cross.cycle.createdDate if hasattr(cross.cycle, "createdDate") else None,
                phases=output_stages,
            ),
            report.triggered,
        )

    def _rescale_green_times(self, green_times: np.ndarray, target_total: int) -> np.ndarray:
        green_times = np.clip(green_times, self.min_green, self.max_green)

        current_sum = np.sum(green_times)
        if current_sum > 0 and current_sum != target_total:
            green_times = green_times * (target_total / current_sum)
            green_times = np.maximum(green_times, self.min_green)

            excess = np.sum(green_times) - target_total
            if abs(excess) > 0.5:
                above_min = green_times - self.min_green
                above_total = np.sum(above_min)
                if above_total > 0:
                    green_times -= above_min * (excess / above_total)

        int_vals = np.floor(green_times).astype(int)
        remainder = int(target_total - np.sum(int_vals))

        if remainder > 0:
            fractional = green_times - int_vals
            indices = np.argsort(fractional)[::-1]
            for i in range(min(remainder, len(indices))):
                int_vals[indices[i]] += 1
        elif remainder < 0:
            indices = np.argsort(int_vals)[::-1]
            for i in range(-remainder):
                idx = indices[i % len(indices)]
                if int_vals[idx] > self.min_green:
                    int_vals[idx] -= 1

        return int_vals
