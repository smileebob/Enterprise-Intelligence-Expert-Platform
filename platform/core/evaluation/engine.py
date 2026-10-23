"""评估引擎（M4，技术方案 03 §3.4）：打分器注册表 + 标准层 + 校准协议 + 双层护栏 + 置信度。

- 规则初算：插件打分器（D1–D6 领域公式，注册表解析）；
- 大模型校准（协议限定）：输入只含规则基线分与证据引用（不含权重与标准细则）；
  严格 JSON + Pydantic 双重校验；delta = clamp(±10)；原始 |delta|>15 → 不应用、不入库总分、挂
  `needs_human_calibration` 进人工队列；校准永不修改权重；temperature=0；失败降级为规则分（L1）。
- 护栏：结论级（BR-05：<5 不出结论/5–9 低/10–29 中/≥30 高）+ 维度级（D4 n<2 参考级）。
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor
from core.abstraction.errors import LlmUnavailableError
from core.domains import DomainPlugin
from core.runtime import eval_repo, get_domain_registry, get_model_gateway

logger = logging.getLogger(__name__)

DIMENSION_GUARDRAIL_N2 = 2
BR05_BINS = [(5, "参考级，不做强结论（BR-05：支撑样本 {n} 份 <5）"), (10, "低置信（BR-05：支撑样本 {n} 份）"), (30, "中置信（BR-05：支撑样本 {n} 份）"), (10**9, "高置信（BR-05：支撑样本 {n} 份）")]

CALIBRATION_SYSTEM_PROMPT = """你是评估校准器。根据规则基线分与证据引用，对边界案例给出小幅校准建议。
约束：只允许输出 JSON；每条校准必须给出 rationale 与非空 citations；delta 为整数且在 -10..10；
不得评论权重与评分标准本身的合理性。输出格式：{"calibrations":[{"dim":"D1","delta":3,"rationale":"…","citations":["EXP-2201"]}]}"""


class CalibrationItem(BaseModel):
    dim: str
    delta: int  # 原始值可超幅；>15 由业务逻辑拒绝应用并转人工（不在此拒收）
    rationale: str = Field(min_length=1)
    citations: list[str] = Field(min_length=1)


class CalibrationResponse(BaseModel):
    calibrations: list[CalibrationItem] = Field(default_factory=list)


class EvaluationEngine:
    def __init__(self, db: Session, gateway=None, registry: Any | None = None):
        self.db = db
        self.gateway = gateway or get_model_gateway()
        self.registry = registry or get_domain_registry()
        self.repo = eval_repo(db)

    # ---------------- 评估 ----------------
    async def evaluate(
        self,
        request: dict[str, Any],
        *,
        actor: Actor | None = None,
        idempotency_key: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        domain_id = request.get("domain_id", "chemical")
        plugin = self.registry.require(domain_id)
        object_ref = dict(request.get("object_ref") or {})
        scenario = request.get("scenario") or "默认"

        if idempotency_key:
            existing = self.repo.get_result_by_idempotency(idempotency_key)
            if existing is not None:
                return existing

        assembler = importlib.import_module(f"domains.{domain_id}.evaluation.assembler")
        inputs = assembler.build_scorer_inputs(self.db, object_ref)

        dimensions, compliance_flags, risk_flags, dim_guardrails = self._run_scorers(plugin, inputs)

        weights, standard_id, standard_version = self._weights_for(plugin, scenario)
        baseline_total = round(
            sum(dimensions[code]["score"] * weights.get(code, 0) / 100 for code in dimensions), 1
        )

        calibration = await self._calibrate(request, plugin, dimensions, baseline_total)
        from core.metrics import EVAL_CALIBRATION

        EVAL_CALIBRATION.labels(applied=str(bool(calibration.get("applied"))).lower()).inc()

        # 固定顺序：校准 → 维度硬约束（D6 等）→ 加权总分（06-C §4.2）
        self._apply_hard_caps(dimensions)
        total = round(sum(dimensions[code]["score"] * weights.get(code, 0) / 100 for code in dimensions), 1)

        confidence = self._confidence(inputs, request)
        guardrails = self._guardrails(inputs, dim_guardrails, calibration)

        result: dict[str, Any] = {
            "eval_id": f"EV-{uuid.uuid4().hex[:16]}",
            "domain_id": domain_id,
            "object_type": request.get("object_type", "solution"),
            "object_ref": object_ref,
            "standard_id": standard_id,
            "standard_version": standard_version,
            "scenario": scenario,
            "baseline_total": baseline_total,
            "total_score": total,
            "confidence": confidence,
            "dimensions": dimensions,
            "dimension_scores": {code: dimensions[code] for code in dimensions},
            "weights": weights,
            "risk_flags": risk_flags,
            "compliance_flags": compliance_flags,
            "guardrails": guardrails,
            "calibration": calibration,
            "scored_by": "rule" if not calibration.get("applied") else "rule+llm",
            "degrade_level": calibration.get("degrade_level", "L0"),
            "idempotency_key": idempotency_key,
        }
        if persist:
            try:
                self.repo.save_result(
                    {
                        "eval_id": result["eval_id"],
                        "domain_id": domain_id,
                        "object_type": result["object_type"],
                        "object_ref": object_ref,
                        "standard_id": standard_id,
                        "scenario": scenario,
                        "dimension_scores": dimensions,
                        "total_score": total,
                        "confidence": confidence,
                        "calibration": calibration,
                        "risk_flags": risk_flags,
                        "compliance_flags": compliance_flags,
                        "guardrails": guardrails,
                        "scored_by": result["scored_by"],
                        "idempotency_key": idempotency_key,
                        "trace_id": request.get("trace_id"),
                    }
                )
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                if idempotency_key:
                    existing = self.repo.get_result_by_idempotency(idempotency_key)
                    if existing is not None:
                        return existing
                raise
        return result

    # ---------------- 打分器注册表 ----------------
    def _run_scorers(self, plugin: DomainPlugin, inputs: dict[str, Any]) -> tuple[dict, list, list, list]:
        dimensions: dict[str, Any] = {}
        compliance_flags: list[dict[str, Any]] = []
        risk_flags: list[dict[str, Any]] = []
        dim_guardrails: list[str] = []
        for dim in plugin.dimensions:
            scorer = plugin.resolve_scorer(dim["scorer"])
            outcome = scorer(inputs)
            code = dim["code"]
            dimensions[code] = {
                "code": code,
                "name": dim.get("name", code),
                "score": outcome.get("score", 0.0),
                "weight": dim.get("default_weight", 0),
                "evidence_refs": outcome.get("evidence_refs", []),
                "deductions": outcome.get("deductions", []),
                "detail": outcome.get("detail", {}),
            }
            compliance_flags.extend(outcome.get("compliance_flags", []))
            detail = outcome.get("detail", {})
            for risk in detail.get("risks", []) if isinstance(detail, dict) else []:
                if isinstance(risk, dict) and not risk.get("mitigated"):
                    risk_flags.append({"dim": code, "reason": risk.get("reason", ""), "points": risk.get("points", 0)})
            if code == "D4" and detail.get("n_lt2"):
                dim_guardrails.append(
                    f"D4 支撑案例 {detail.get('n_cases', 0)} 份（不足 {DIMENSION_GUARDRAIL_N2} 份）：参考级（维度级护栏）"
                )
        return dimensions, compliance_flags, risk_flags, dim_guardrails

    @staticmethod
    def _apply_hard_caps(dimensions: dict[str, Any]) -> None:
        """领域打分器可声明 detail.hard_cap（如 D6 合规上限）；校准后必须重新执行。"""
        for dim in dimensions.values():
            cap = (dim.get("detail") or {}).get("hard_cap")
            if isinstance(cap, (int, float)) and dim.get("score", 0.0) > float(cap):
                dim["score"] = round(float(cap), 1)
                dim["hard_cap_applied"] = float(cap)

    def _weights_for(self, plugin: DomainPlugin, scenario: str) -> tuple[dict[str, int], str, str]:
        standard = self.repo.get_active_standard(plugin.domain_id, plugin.eval_config.get("object_type", "solution"))
        if standard is not None and standard.scenario_profiles.get(scenario):
            return (
                {k: int(v) for k, v in standard.scenario_profiles[scenario].items()},
                standard.standard_id,
                standard.version or standard.standard_id,
            )
        profiles = plugin.scenario_profiles
        weights = profiles.get(scenario) or profiles.get("默认") or {dim["code"]: dim.get("default_weight", 0) for dim in plugin.dimensions}
        return {k: int(v) for k, v in weights.items()}, plugin.standard_ref, plugin.standard_ref

    # ---------------- 校准协议 ----------------
    async def _calibrate(
        self,
        request: dict[str, Any],
        plugin: DomainPlugin,
        dimensions: dict[str, Any],
        total: float,
    ) -> dict[str, Any]:
        triggered, reason = self._calibration_trigger(request, plugin, dimensions)
        if not triggered:
            return {"applied": False, "reason": "未触发校准条件", "items": [], "degrade_level": "L0"}

        baseline = [
            {
                "dim": code,
                "score": dimensions[code]["score"],
                "citations": dimensions[code]["evidence_refs"][:5],
            }
            for code in dimensions
        ]
        messages = [
            {"role": "system", "content": CALIBRATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"domain": plugin.domain_id, "trigger": reason, "baseline": baseline, "total": total},
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            # 超时按配置（LLM_TIMEOUT_S）生效：推理型模型校准耗时可能远超短超时
            chat = await self.gateway.chat(messages, temperature=0.0, json_mode=True)
        except LlmUnavailableError:
            return {"applied": False, "reason": "模型不可用，降级为规则分（L1）", "items": [], "degrade_level": "L1"}

        try:
            parsed = CalibrationResponse.model_validate(json.loads(chat.content))
        except (ValueError, ValidationError):
            logger.warning("calibration response invalid, rejected", exc_info=True)
            return {"applied": False, "reason": "校准响应不合规（Schema 校验拒绝）", "items": [], "degrade_level": chat.degrade_level}

        # 整批验证：任一维度/增量/理由/引用不合法 → 整批拒绝，不做部分应用
        rejected: list[dict[str, Any]] = []
        needs_human = False
        available_all = set().union(
            *(set(dim.get("evidence_refs") or []) for dim in dimensions.values())
        )
        for item in parsed.calibrations:
            reason_invalid = ""
            if item.dim not in dimensions:
                reason_invalid = "维度不存在"
            elif not str(item.rationale or "").strip():
                reason_invalid = "rationale 为空"
            elif not item.citations:
                reason_invalid = "缺少引用"
            else:
                invalid_refs = [cid for cid in item.citations if cid not in available_all]
                if invalid_refs:
                    reason_invalid = f"引用不属于本次可用证据: {','.join(invalid_refs[:3])}"
            if reason_invalid:
                rejected.append({"dim": item.dim, "reason": reason_invalid})
            if abs(item.delta) > 15:
                needs_human = True
            if item.delta == 0:
                rejected.append({"dim": item.dim, "reason": "delta 为 0"})
        if rejected or needs_human:
            return {
                "applied": False,
                "trigger": reason,
                "items": [],
                "rejected_items": rejected,
                "needs_human_calibration": needs_human,
                "reason": "校准响应不合规，整批未应用",
                "degrade_level": chat.degrade_level,
            }

        applied_items = []
        for item in parsed.calibrations:
            delta = max(-10, min(10, item.delta))  # ±10 强制限幅
            dimensions[item.dim]["score"] = round(max(0.0, min(100.0, dimensions[item.dim]["score"] + delta)), 1)
            dimensions[item.dim]["calibrated_delta"] = delta
            applied_items.append({"dim": item.dim, "delta": delta, "rationale": item.rationale, "citations": item.citations})

        return {
            "applied": bool(applied_items),
            "trigger": reason,
            "items": applied_items,
            "needs_human_calibration": False,
            "degrade_level": chat.degrade_level,
        }

    def _calibration_trigger(self, request: dict[str, Any], plugin: DomainPlugin, dimensions: dict[str, Any]) -> tuple[bool, str]:
        if request.get("force_calibrate"):
            return True, "显式触发"
        if request.get("conflicts"):
            return True, "方案涉及内外部知识冲突"
        if request.get("mixed_fabric"):
            return True, "混纺/特殊品种面料"
        d6 = dimensions.get("D6", {})
        if d6.get("detail", {}).get("triggered"):
            return True, "命中合规限制（需解释性校准）"
        d1 = dimensions.get("D1", {})
        if len(d1.get("detail", {}).get("items", [])) >= 3:
            return True, "复合需求（多目标性能）"
        return False, ""

    # ---------------- 置信度与护栏 ----------------
    def _confidence(self, inputs: dict[str, Any], request: dict[str, Any]) -> float:
        avg_conf = float(inputs.get("avg_conf") or 0.7)
        object_ref = request.get("object_ref") or {}
        expected = 4
        filled = sum(1 for key in ("products", "formula", "process", "target_market") if object_ref.get(key))
        completeness = min(1.0, filled / expected + 0.2)
        conflicts = request.get("conflicts") or []
        if not conflicts:
            certainty = 1.0
        else:
            unresolved = any((c or {}).get("status") not in ("resolved", None) for c in conflicts)
            certainty = 0.4 if unresolved else 0.7
        return round(avg_conf * 0.5 + completeness * 0.3 + certainty * 0.2, 3)

    def _guardrails(self, inputs: dict[str, Any], dim_guardrails: list[str], calibration: dict[str, Any]) -> list[str]:
        guardrails: list[str] = list(dim_guardrails)
        n = int(inputs.get("n_cases", 0))
        for threshold, template in BR05_BINS:
            if n < threshold:
                guardrails.append(template.format(n=n))
                break
        if calibration.get("needs_human_calibration"):
            guardrails.append("校准增量原值超幅（>15）：未应用、未入总分，已挂 needs_human_calibration 转人工队列")
        if calibration.get("applied") is False and calibration.get("degrade_level") in ("L1", "L2"):
            guardrails.append("校准不可用，结果以规则分输出（简化分析）")
        return guardrails

    # ---------------- 人工确认/修正（P6.5，回流 M7） ----------------
    def confirm(self, eval_id: str, action: dict[str, Any], actor: Actor | None) -> dict[str, Any]:
        result = self.repo.get_result(eval_id)
        if result is None:
            raise KeyError(f"评估结果不存在: {eval_id}")
        from core.evaluation.models import EvalResult

        row = self.db.get(EvalResult, eval_id)
        if row is None:
            raise KeyError(f"评估结果不存在: {eval_id}")
        action_type = action.get("action", "confirm")
        row.confirmed_by = actor.user_id if actor else "anonymous"
        row.confirm_action = {
            "action": action_type,
            "corrections": action.get("corrections", {}),
            "reason": action.get("reason", ""),
        }
        if action_type == "modify" and action.get("corrections"):
            self._record_correction_feedback(eval_id, action, actor)
        self.db.commit()
        return self.repo.get_result(eval_id)

    def _record_correction_feedback(self, eval_id: str, action: dict[str, Any], actor: Actor | None) -> None:
        from core.growth.models import GrowthFeedback

        self.db.add(
            GrowthFeedback(
                feedback_id=f"FB-{uuid.uuid4().hex[:12]}",
                eval_id=eval_id,
                user_id=actor.user_id if actor else "anonymous",
                domain_id="chemical",
                rating="modified",
                correction=json.dumps(action.get("corrections", {}), ensure_ascii=False),
                comment=action.get("reason", ""),
                sample_type="correction",
            )
        )
        self.db.flush()
