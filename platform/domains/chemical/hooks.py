"""化学领域钩子适配器：把原有规划/补证/装配/持久化/composer 行为迁出引擎。"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from core.orchestration.hooks import (
    EvalRequestSpec,
    EvidenceView,
    ExecutionContext,
    GenerationContext,
    NeedView,
    PluginSnapshot,
    ToolIntent,
    ToolResultView,
    TurnView,
)

CODE_VERSION = "chemical-hooks-v1"

CAPABILITIES = frozenset(
    {
        "plan_tools",
        "bind_tool_args",
        "evidence_refs_from_result",
        "assemble_evaluation",
        "persist_outcome",
        "compose",
    }
)


def _market_text(market: str) -> str:
    if market == "eu":
        return "出口欧盟"
    if market == "domestic":
        return "内销"
    return ""


def plan_tools(need: NeedView, snapshot: PluginSnapshot) -> list[ToolIntent]:
    """规划只产意图与静态参数；证据相关参数留到 bind 阶段（F01）。"""
    intents: list[ToolIntent] = []
    if need.market != "unknown":
        intents.append(ToolIntent("check_compliance"))
    if need.mode in ("analysis", "diagnostic"):
        intents.append(ToolIntent("search_experiments"))
    if need.mode == "diagnostic":
        keyword = "低黄变 亲水" if "黄变" in need.user_msg else " ".join(need.target_performance[:2])
        intents.append(ToolIntent("search_products", {"query": keyword or "柔软"}))
    return [replace(intent, invocation_index=index) for index, intent in enumerate(intents)]


def bind_tool_args(intent: ToolIntent, evidence: EvidenceView, snapshot: PluginSnapshot) -> dict[str, Any]:
    """检索完成后绑定证据参数（保持原引擎的取数与顺序）。"""
    args = dict(intent.static_args)
    if intent.tool_name == "check_compliance":
        product_ids = [
            str((item.meta or {}).get("record_id"))
            for item in evidence.evidence
            if (item.meta or {}).get("record_type") == "products"
        ][:4]
        args.update({"product_ids": product_ids, "target_market": _market_text(evidence.need.market)})
    elif intent.tool_name == "search_experiments":
        metrics = evidence.need.target_performance[:1] or ("",)
        args.update({"metric": metrics[0]})
    return args


def evidence_refs_from_result(
    intent: ToolIntent, result: ToolResultView, snapshot: PluginSnapshot
) -> list[str]:
    if intent.tool_name == "search_products" and result.ok and isinstance(result.data, list):
        return [str(item.get("product_id")) for item in result.data if item.get("product_id")]
    return []


def assemble_evaluation(turn: TurnView, snapshot: PluginSnapshot) -> EvalRequestSpec | None:
    """组装评估对象（保持现有前两产品规则与配方/工艺引用）。"""
    from domains.chemical.composer import CATEGORY_BY_PERFORMANCE

    need = turn.need
    wanted = {
        CATEGORY_BY_PERFORMANCE[item]
        for item in need.target_performance
        if item in CATEGORY_BY_PERFORMANCE
    }
    named = set(need.product_ids)
    product_ids: list[str] = []
    for item in turn.evidence:
        meta = item.meta or {}
        if meta.get("record_type") != "products":
            continue
        fields = meta.get("fields", {})
        record_id = str(meta.get("record_id", ""))
        if record_id in product_ids:
            continue
        if record_id in named or (not wanted or fields.get("category") in wanted):
            product_ids.append(record_id)
        if len(product_ids) >= 2:
            break
    if not product_ids:
        return None
    formula_id = ""
    process_ref = ""
    for item in turn.evidence:
        meta = item.meta or {}
        if meta.get("record_type") == "formulas" and not formula_id:
            formula_id = str(meta.get("record_id", ""))
        if meta.get("record_type") == "processes" and not process_ref:
            process_ref = str(meta.get("record_id", ""))
        if meta.get("record_type") == "products" and str(meta.get("record_id", "")) == product_ids[0]:
            process_ref = str(meta.get("fields", {}).get("process_ref", "")) or process_ref
    object_ref = {
        "products": product_ids,
        "formula": formula_id,
        "process": process_ref,
        "target_market": _market_text(need.market),
        "need_key": str(need.slots.get("目标性能") or ""),
    }
    return EvalRequestSpec(object_type="solution", object_ref=object_ref, scenario=need.scenario)


def persist_outcome(
    db: Any, ctx: ExecutionContext, spec: EvalRequestSpec, snapshot: PluginSnapshot
) -> EvalRequestSpec:
    """领域持久化由领域钩子执行，调用方控制事务（钩子不 commit）。"""
    from domains.chemical.models import ChemSolution

    solution_id = f"SOL-{uuid.uuid4().hex[:10]}"
    row = db.get(ChemSolution, solution_id) or ChemSolution(solution_id=solution_id)
    db.add(row)
    row.session_id = ctx.session_id
    row.message_id = ctx.message_id
    row.scenario = spec.scenario
    row.recommended_products = spec.object_ref.get("products", [])
    row.recommended_process = {
        "process": spec.object_ref.get("process", ""),
        "formula": spec.object_ref.get("formula", ""),
    }
    row.trace_id = ctx.run_id
    db.flush()
    return replace(spec, object_ref={**spec.object_ref, "solution_id": solution_id})


def compose(ctx: GenerationContext, snapshot: PluginSnapshot) -> Any:
    from domains.chemical.composer import compose as chemical_compose

    return chemical_compose(
        ctx.need,
        ctx.fused,
        eval_result=ctx.eval_result,
        tool_results=list(ctx.tool_results),
        degrade_level=ctx.degrade_level,
    )
