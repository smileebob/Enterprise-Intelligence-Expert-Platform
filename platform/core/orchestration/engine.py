"""智能体编排引擎（M2，技术方案 03 §3.2）：状态图节点 + 中断/恢复 + 检查点 + 重规划 N=2。

流程：输入(路由层)→澄清→规划→检索→融合→工具→评估→生成→自检→(重规划)→输出。
中断点：澄清追问 / 危险工具确认；检查点落业务库（断点续跑/审计）。
"""

from __future__ import annotations

import importlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor, Evidence, FusedEvidence
from core.abstraction.errors import LlmUnavailableError
from core.domains import DomainPlugin, DomainRegistry
from core.gateway.service import ToolCtx, ToolGateway
from core.guardrails.hooks import HookContext
from core.guardrails.output_hook import OutputGuard
from core.guardrails.retrieval_hook import apply_retrieval_guard
from core.knowledge.service import KnowledgeService
from core.memory.service import MemoryService, RecallCtx
from core.orchestration import checkpoints
from core.orchestration.models import ChatSession
from core.orchestration.need_extractor import (
    extract_need,
    merge_history_context,
    merge_resume_answer,
    required_slots,
)
from core.orchestration.state import StepTrace, TurnState

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_DEGRADE_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


@dataclass
class TurnOutcome:
    state: TurnState
    events: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    interrupted: bool = False
    interrupt: dict[str, Any] | None = None
    checkpoint_id: int | None = None


class Orchestrator:
    def __init__(self, db: Session, gateway=None, registry: DomainRegistry | None = None):
        from core.runtime import get_domain_registry, get_model_gateway

        self.db = db
        self.gateway = gateway or get_model_gateway()
        self.registry = registry or get_domain_registry()
        self.knowledge = KnowledgeService(db, self.gateway, self.registry)
        self.memory = MemoryService(db, self.gateway, self.registry)
        self.tools = ToolGateway(db)
        self.output_guard = OutputGuard(db)

    # ---------------- 对外入口 ----------------
    async def run_turn(
        self,
        session: ChatSession,
        user_msg: str,
        actor: Actor,
        *,
        message_id: str = "",
        emit: EmitFn | None = None,
    ) -> TurnOutcome:
        plugin = self.registry.require(session.domain_id)
        from app.common.context import get_trace_id

        trace_id = get_trace_id()
        if not trace_id or trace_id == "-":
            trace_id = f"T-{uuid.uuid4().hex[:12]}"
        state = TurnState(
            session_id=session.session_id,
            turn_id=trace_id,
            user_id=actor.user_id,
            role_id=actor.role_id,
            domain_id=session.domain_id,
            message_id=message_id,
            user_msg=user_msg,
        )
        state.history = self._load_history(session.session_id, exclude_message_id=message_id)
        return await self._pipeline(state, plugin, actor, emit, start="clarify")

    def _load_history(self, session_id: str, *, exclude_message_id: str = "") -> list[dict[str, str]]:
        """装载会话滑窗（最近 N 条消息，时序升序；剔除本轮消息）。单条截断以控检查点体积。"""
        from app.config import get_settings
        from core.orchestration.session import ChatSessionStore

        window = max(int(get_settings().ORCH_HISTORY_WINDOW), 0)
        if window <= 0:
            return []
        rows = ChatSessionStore(self.db).recent_messages(session_id, limit=window)
        return [
            {"role": m.role, "content": (m.content or "")[:500]}
            for m in rows
            if m.message_id != exclude_message_id and (m.content or "").strip()
        ]

    def _raise_degrade(self, state: TurnState, level: str) -> None:
        """按严重度上浮回合降级层级（只升不降）。"""
        if _DEGRADE_RANK.get(level, 0) > _DEGRADE_RANK.get(str(state.degrade_level), 0):
            state.degrade_level = level

    async def resume(self, session_id: str, resume_value: Any, actor: Actor, *, emit: EmitFn | None = None) -> TurnOutcome:
        loaded = checkpoints.load_latest_interrupted(self.db, session_id)
        if loaded is None:
            raise KeyError("没有待恢复的中断对话")
        checkpoint_id, state = loaded
        plugin = self.registry.require(state.domain_id)
        state.interrupted = False
        state.resume_value = resume_value
        if not state.history:
            state.history = self._load_history(state.session_id)
        checkpoints.mark_done(self.db, checkpoint_id)
        if state.interrupt_kind == "clarify" and state.need:
            state.need = merge_resume_answer(state.need, str(resume_value or ""), plugin)
        # 从规划继续（澄清已解决）
        return await self._pipeline(state, plugin, actor, emit, start="plan", checkpoint_id=checkpoint_id)

    # ---------------- 主管道 ----------------
    async def _pipeline(
        self,
        state: TurnState,
        plugin: DomainPlugin,
        actor: Actor,
        emit: EmitFn | None,
        *,
        start: str = "clarify",
        checkpoint_id: int | None = None,
    ) -> TurnOutcome:
        events: list[dict[str, Any]] = []

        async def emit_event(kind: str, data: dict[str, Any]) -> None:
            events.append({"event": kind, "data": data})
            if emit is not None:
                await emit(kind, data)

        order = ["clarify", "plan", "retrieve", "tools", "evaluate", "generate", "output"]
        start_index = order.index(start) if start in order else 0

        import time as _time

        from core.metrics import AGENT_NODE_DURATION, DEGRADE_LEVEL

        try:
            for node in order[start_index:]:
                handler = getattr(self, f"_node_{node}")
                node_started = _time.perf_counter()
                try:
                    await handler(state, plugin, actor, emit_event)
                finally:
                    AGENT_NODE_DURATION.labels(node=node).observe(_time.perf_counter() - node_started)
                if state.interrupted:
                    checkpoint_id = checkpoints.save_checkpoint(
                        self.db, state, node, status="interrupted", interrupt_kind=state.interrupt_kind
                    )
                    return TurnOutcome(
                        state=state,
                        events=events,
                        interrupted=True,
                        interrupt={"kind": state.interrupt_kind, **state.interrupt_payload},
                        checkpoint_id=checkpoint_id,
                    )
                if node == "generate":
                    await self._self_check_loop(state, plugin, actor, emit_event)
        except LlmUnavailableError as exc:  # pragma: no cover - 网络态
            self._raise_degrade(state, "L2")
            await emit_event("error", {"code": "LLM_UNAVAILABLE", "message": str(exc)})

        final_text = str(state.plan.get("final_text", ""))
        DEGRADE_LEVEL.labels(level=str(state.degrade_level)).inc()
        await emit_event(
            "done",
            {
                "code": "SUCCESS",
                "data": {
                    "message_id": state.assistant_message_id,
                    "citations": state.draft.citations if state.draft else [],
                    "conflicts": [c.conflict_id for c in state.conflicts],
                    "eval": state.eval_result,
                    "degrade_level": state.degrade_level,
                    "annotations": (state.draft.annotations if state.draft else []),
                    "interrupted": False,
                },
            },
        )
        return TurnOutcome(
            state=state,
            events=events,
            text=final_text,
            interrupted=False,
            interrupt=None,
            checkpoint_id=checkpoint_id,
        )

    # ---------------- 节点：澄清 ----------------
    async def _node_clarify(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        state.need = extract_need(state.user_msg, plugin, state.domain_id)
        need = state.need
        # 多轮指代消解：会话滑窗内补齐缺失定位槽位（确定性规则，见 need_extractor.merge_history_context）
        if state.history:
            inherited = merge_history_context(need, state.history, plugin)
            if inherited:
                state.plan["inherited_slots"] = inherited
                note = "；".join(f"{k}={v}" for k, v in inherited.items() if not k.startswith("__"))
                summary = f"已继承会话上下文槽位（{note or '市场=' + inherited.get('__market__', '')}）"
                state.trace.append(StepTrace(node="context", status="done", summary=summary))
                await emit("step", {"node": "context", "status": "done", "summary": summary})
        missing_required = [slot for slot in required_slots(need, plugin) if slot not in need.slots]
        if missing_required and not state.plan.get("clarify_asked"):
            state.plan["clarify_asked"] = True
            state.interrupted = True
            state.interrupt_kind = "clarify"
            state.interrupt_payload = {
                "question": f"为了给出可落地的方案，请先补充：{'、'.join(missing_required)}。",
                "missing_slots": missing_required,
                "slots_required": plugin.clarify_slots,
            }
            state.trace.append(StepTrace(node="clarify", status="interrupted", summary=state.interrupt_payload["question"]))
            await emit("clarify", state.interrupt_payload)
            return
        filled = {k: v for k, v in need.slots.items() if not k.startswith("__")}
        state.trace.append(
            StepTrace(
                node="clarify",
                status="done",
                summary=f"需求解析完成（{'；'.join(f'{k}={v}' for k, v in filled.items()) or '基础信息充分'}；场景={need.scenario}）",
            )
        )
        await emit("step", {"node": "clarify", "status": "done", "summary": state.trace[-1].summary})

    # ---------------- 节点：规划 ----------------
    async def _node_plan(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        need = state.need
        planned_tools = []
        tool_args: dict[str, dict[str, Any]] = {}
        if need is not None:
            if state.domain_id == "chemical":
                if need.market != "unknown":
                    planned_tools.append("check_compliance")
                if need.slots.get("__mode__") in ("analysis", "diagnostic"):
                    planned_tools.append("search_experiments")
                if need.slots.get("__mode__") == "diagnostic":
                    # 诊断类：补充低黄变/亲水替代方向的产品检索（结果回填证据，带引用锚点）
                    planned_tools.append("search_products")
                    keyword = "低黄变 亲水" if "黄变" in state.user_msg else " ".join(need.target_performance[:2])
                    tool_args["search_products"] = {"query": keyword or "柔软"}
        state.plan.update(
            {
                "tools": planned_tools,
                "tool_args": tool_args,
                "evaluate": bool(need and need.evaluate),
                "scenario": need.scenario if need else "默认",
            }
        )
        # 推理自成长：负样本纠错规则注入（历史否决/修正沉淀为负向约束）
        if not state.correction_rules:
            try:
                from core.growth.service import GrowthService

                state.correction_rules = GrowthService(self.db).active_rule_texts(state.domain_id, limit=3)
            except Exception:
                logger.debug("load correction rules failed", exc_info=True)
        summary = f"规划完成：检索分区=内部优先；工具={planned_tools or '无'}；评估={'是' if state.plan['evaluate'] else '否'}（场景={state.plan['scenario']}）"
        state.trace.append(StepTrace(node="plan", status="done", summary=summary))
        await emit("step", {"node": "plan", "status": "done", "summary": summary})

    # ---------------- 节点：检索（分区召回 + 记忆注入） ----------------
    def _retrieval_query(self, state: TurnState) -> str:
        """检索查询：指代轮并入继承槽位值（如「换成棉的呢？ 防水 无氟 耐洗」），保证召回延续上一轮主题。"""
        inherited = state.plan.get("inherited_slots") or {}
        if not isinstance(inherited, dict) or not inherited:
            return state.user_msg
        extras = [str(v) for k, v in inherited.items() if not k.startswith("__") and v]
        if not extras:
            return state.user_msg
        return f"{state.user_msg} {' '.join(extras).replace('、', ' ')}"

    async def _node_retrieve(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        need = state.need
        query_text = self._retrieval_query(state)
        permission_max = apply_retrieval_guard(
            self.db, actor, state.domain_id, query_text, session_id=state.session_id, trace_id=state.turn_id
        )
        query_vec = await self.knowledge.embed_query(query_text)
        fused = await self.knowledge.retrieve(
            query_text,
            state.domain_id,
            permission_max=permission_max,
            market=need.market if need else None,
            need=need.to_dict() if need else None,
            query_vec=query_vec,
        )
        state.evidence = fused.evidence
        state.conflicts = fused.conflicts
        if state.conflicts:
            from core.metrics import CONFLICT_DETECTED

            for detected in state.conflicts:
                CONFLICT_DETECTED.labels(scenario=detected.conflict_id).inc()
        state.plan["override_flags"] = fused.override_flags
        state.plan["fuse_notes"] = fused.notes

        # 记忆注入（用户/企业/领域分层 top-k）
        memory_items = await self.memory.recall(
            RecallCtx(
                actor=actor,
                domain_id=state.domain_id,
                query=state.user_msg,
                query_vec=query_vec,
                session_id=state.session_id,
            )
        )
        state.memory_items = [{"memory_id": m.memory_id, "layer": m.layer, "content": m.content} for m in memory_items]

        stats = fused.partition_stats
        summary = f"分区召回 {len(fused.evidence)} 条（内部 {stats.get('internal', 0)}/外部 {stats.get('external', 0)}）"
        if memory_items:
            summary += f"；记忆注入 {len(memory_items)} 条"
        state.trace.append(StepTrace(node="retrieve", status="done", summary=summary))
        await emit("step", {"node": "retrieve", "status": "done", "summary": summary})

        fuse_summary = "融合完成：按优先级全序排序，逐条赋予引用锚点"
        if fused.conflicts:
            fuse_summary = f"检出 {len(fused.conflicts)} 处冲突（{'、'.join(c.conflict_id for c in fused.conflicts)}），已按内部优先裁决并标注"
        if fused.override_flags:
            fuse_summary += "；命中合规例外（以外部法规为准）"
        state.trace.append(StepTrace(node="fuse", status="done", summary=fuse_summary))
        await emit("step", {"node": "fuse", "status": "done", "summary": fuse_summary})

    # ---------------- 节点：工具调用（经网关 Hook③） ----------------
    async def _node_tools(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        planned = state.plan.get("tools", [])
        tool_results: list[dict[str, Any]] = []
        if not planned:
            state.plan["tool_results"] = tool_results
            return
        ctx = ToolCtx(
            db=self.db,
            actor=actor,
            session_id=state.session_id,
            domain_id=state.domain_id,
            trace_id=state.turn_id,
            gateway=self.gateway,
            registry=self.registry,
            extra={"market": state.need.market if state.need else ""},
        )
        product_ids = [
            str((e.meta or {}).get("record_id"))
            for e in state.evidence
            if (e.meta or {}).get("record_type") == "products"
        ][:4]
        tool_args: dict[str, dict[str, Any]] = state.plan.get("tool_args", {})
        for tool_name in planned:
            try:
                if tool_name == "check_compliance":
                    result = await self.tools.invoke(
                        tool_name,
                        {"product_ids": product_ids, "target_market": self._market_text(state)},
                        ctx,
                    )
                elif tool_name == "search_experiments":
                    metric = (state.need.target_performance[:1] or [""])[0] if state.need else ""
                    result = await self.tools.invoke(tool_name, {"metric": metric}, ctx)
                elif tool_name in tool_args:
                    result = await self.tools.invoke(tool_name, dict(tool_args[tool_name]), ctx)
                else:
                    result = await self.tools.invoke(tool_name, {}, ctx)
            except Exception:
                logger.warning("tool %s failed, continue pipeline", tool_name, exc_info=True)
                continue
            tool_results.append({"tool": tool_name, "ok": result.ok, "data": result.data, "meta": result.meta})
            # 工具结果回填证据（带引用锚点）：产品类结果补齐其知识块
            if tool_name == "search_products" and result.ok and isinstance(result.data, list):
                self._augment_evidence_from_products(state, result.data)
        state.plan["tool_results"] = tool_results
        summary = f"工具调用 {len(tool_results)} 次（经权限网关与审计）"
        state.trace.append(StepTrace(node="tool", status="done", summary=summary))
        await emit("step", {"node": "tool", "status": "done", "summary": summary})

    def _augment_evidence_from_products(self, state: TurnState, products: list[dict[str, Any]]) -> None:
        """工具结果（产品）→ 知识块回填证据（保持可追溯引用；供方案装配与输出校验使用）。"""
        record_ids = [str(p.get("product_id", "")) for p in products if p.get("product_id")]
        if not record_ids:
            return
        existing = {e.chunk_id for e in state.evidence}
        fetched = self.knowledge.repo.chunks_by_refs(record_ids, state.domain_id)
        next_index = len(state.evidence) + 1
        for chunk in fetched:
            if chunk.chunk_id in existing:
                continue
            state.evidence.append(
                Evidence(
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    source_type=chunk.source_type,
                    confidence=chunk.confidence,
                    citation_id=f"C{next_index}",
                    source_ref=chunk.source_ref,
                    score=chunk.score,
                    category=chunk.category,
                    meta=chunk.chunk.meta or {},
                )
            )
            existing.add(chunk.chunk_id)
            next_index += 1

    def _market_text(self, state: TurnState) -> str:
        if state.need and state.need.market == "eu":
            return "出口欧盟"
        if state.need and state.need.market == "domestic":
            return "内销"
        return ""

    # ---------------- 节点：六维评估（可选） ----------------
    async def _node_evaluate(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        need = state.need
        if not need or not state.plan.get("evaluate"):
            return
        object_ref = self._assemble_object_ref(state)
        if not object_ref.get("products"):
            state.plan["evaluate"] = False
            return
        solution = self._save_solution(state, object_ref)
        object_ref["solution_id"] = solution
        state.plan["object_ref"] = object_ref
        from core.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine(self.db, gateway=self.gateway, registry=self.registry)
        try:
            eval_result = await engine.evaluate(
                {
                    "domain_id": state.domain_id,
                    "object_type": "solution",
                    "object_ref": object_ref,
                    "scenario": need.scenario,
                    "need_ref": object_ref.get("need_key"),
                },
                actor=actor,
                idempotency_key=None,
                persist=True,
            )
        except Exception:
            logger.warning("evaluation failed, continue without score", exc_info=True)
            self._raise_degrade(state, "L1")
            return
        state.eval_result = eval_result
        # 降级层级贯穿：live 配置下校准落 L1/L2（模型失败/熔断）时上浮回合 meta（06 §6.5 诚实徽标）；
        # offline/replay 为确定性离线通道（其通道性质由界面「离线模式」标注表达），不上浮
        from adapters.model_gateway import ResilientGateway

        if isinstance(self.gateway, ResilientGateway):
            self._raise_degrade(state, str(eval_result.get("degrade_level") or "L0"))
        state.trace.append(
            StepTrace(node="evaluate", status="done", summary=f"六维评估 {eval_result.get('total_score')}（{need.scenario}；置信度 {eval_result.get('confidence')}）")
        )
        await emit(
            "eval",
            {
                "total_score": eval_result.get("total_score"),
                "scenario": need.scenario,
                "confidence": eval_result.get("confidence"),
                "compliance_flags": eval_result.get("compliance_flags", []),
            },
        )

    def _assemble_object_ref(self, state: TurnState) -> dict[str, Any]:
        need = state.need
        product_ids: list[str] = []
        from domains.chemical.composer import CATEGORY_BY_PERFORMANCE

        wanted = {CATEGORY_BY_PERFORMANCE[p] for p in (need.target_performance if need else []) if p in CATEGORY_BY_PERFORMANCE}
        named = set(need.product_ids if need else [])
        for item in state.evidence:
            meta = item.meta or {}
            if meta.get("record_type") != "products":
                continue
            fields = meta.get("fields", {})
            rid = str(meta.get("record_id", ""))
            if rid in product_ids:
                continue
            if rid in named or (not wanted or fields.get("category") in wanted):
                product_ids.append(rid)
            if len(product_ids) >= 2:
                break
        if not product_ids:
            return {}
        # 配方与工艺：取第一个产品的关联
        formula_id = ""
        process_ref = ""
        for item in state.evidence:
            meta = item.meta or {}
            if meta.get("record_type") == "formulas" and not formula_id:
                formula_id = str(meta.get("record_id", ""))
            if meta.get("record_type") == "processes" and not process_ref:
                process_ref = str(meta.get("record_id", ""))
            if meta.get("record_type") == "products" and str(meta.get("record_id", "")) == product_ids[0]:
                process_ref = str(meta.get("fields", {}).get("process_ref", "")) or process_ref
        return {
            "products": product_ids,
            "formula": formula_id,
            "process": process_ref,
            "target_market": self._market_text(state),
            "need_key": (need.slots.get("目标性能") if need else "") or "",
        }

    def _save_solution(self, state: TurnState, object_ref: dict[str, Any]) -> str:
        from domains.chemical.models import ChemSolution

        solution_id = f"SOL-{uuid.uuid4().hex[:10]}"
        row = self.db.get(ChemSolution, solution_id) or ChemSolution(solution_id=solution_id)
        self.db.add(row)
        row.session_id = state.session_id
        row.message_id = state.message_id
        row.scenario = state.need.scenario if state.need else "默认"
        row.recommended_products = object_ref.get("products", [])
        row.recommended_process = {"process": object_ref.get("process", ""), "formula": object_ref.get("formula", "")}
        row.trace_id = state.turn_id
        self.db.commit()
        return solution_id

    # ---------------- 节点：生成 ----------------
    async def _node_generate(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        composer = importlib.import_module(f"domains.{state.domain_id}.composer")
        fused = FusedEvidence(
            evidence=state.evidence,
            conflicts=state.conflicts,
            partition_stats={},
            override_flags=state.plan.get("override_flags", []),
            notes=state.plan.get("fuse_notes", []),
        )
        draft = composer.compose(
            state.need,
            fused,
            eval_result=state.eval_result,
            tool_results=state.plan.get("tool_results", []),
            degrade_level=state.degrade_level,
        )
        # 记忆复用说明（非事实结论，不占引用）
        if state.memory_items:
            draft.annotations.append(f"已结合您的历史偏好与组织沉淀（记忆复用 {len(state.memory_items)} 条）")
        inherited = state.plan.get("inherited_slots") or {}
        if isinstance(inherited, dict) and inherited:
            note = "；".join(f"{k}={v}" for k, v in inherited.items() if not k.startswith("__"))
            if not note:
                note = f"目标市场={inherited.get('__market__', '')}"
            draft.annotations.append(f"已结合本会话上下文（继承：{note}）")
        if state.correction_rules:
            draft.annotations.append("已应用历史纠错约束：" + "；".join(state.correction_rules[-2:]))
        state.draft = draft
        summary = f"方案草稿生成（{len(draft.all_claims())} 条结论，全部挂引用）"
        state.trace.append(StepTrace(node="generate", status="done", summary=summary))
        await emit("step", {"node": "generate", "status": "done", "summary": summary})

    # ---------------- 自检与重规划（N=2，03 §3.2 实现要点 4） ----------------
    async def _self_check_loop(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        from app.config import get_settings

        max_replan = get_settings().ORCH_MAX_REPLAN
        while True:
            check = self.output_guard.validate_citations_only(state.draft, state.evidence)
            state.self_check = check
            if check["passed"]:
                summary = "自检通过（引用存在、冲突已标注）"
                state.trace.append(StepTrace(node="self_check", status="done", summary=summary))
                await emit("step", {"node": "self_check", "status": "done", "summary": summary})
                return
            problems = []
            if check["unverified_claims"]:
                problems.append(f"{check['unverified_claims']} 条结论缺少有效引用")
            if not check["conflict_noted"]:
                problems.append("冲突未标注")
            if state.replan_count >= max_replan:
                annotation = "证据不足，建议人工核验（自检未通过且重规划已达上限）"
                state.draft.annotations.append(annotation)
                state.trace.append(StepTrace(node="self_check", status="manual", summary=annotation))
                await emit("step", {"node": "self_check", "status": "manual_review", "summary": annotation})
                return
            state.replan_count += 1
            from core.metrics import AGENT_REPLAN

            AGENT_REPLAN.inc()
            state.correction_rules.append("；".join(problems))
            summary = f"自检未通过（{'；'.join(problems)}），重规划第 {state.replan_count} 次"
            state.trace.append(StepTrace(node="self_check", status="replan", summary=summary))
            await emit("step", {"node": "self_check", "status": "replan", "summary": summary})
            await self._node_retrieve(state, plugin, actor, emit)
            await self._node_generate(state, plugin, actor, emit)

    # ---------------- 节点：输出（④ Hook，先校验后外发） ----------------
    async def _node_output(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        hook_ctx = HookContext(
            stage="output",
            actor=actor,
            domain_id=state.domain_id,
            session_id=state.session_id,
            payload={"claims": len(state.draft.all_claims())},
            trace_id=state.turn_id,
        )
        check = self.output_guard.check(
            state.draft,
            state.evidence,
            hook_ctx,
            confidence_threshold=plugin.confidence_threshold,
        )
        self.db.commit()
        state.draft.annotations = list(dict.fromkeys(check.annotations + state.draft.annotations))
        state.plan["final_text"] = check.text
        # 引用合规底线指标：带引用结论占比（06 §6.6，告警基线 <0.95）
        from core.metrics import CITATION_COVERAGE

        total_claims = len(state.draft.all_claims()) if state.draft is not None else 0
        if total_claims:
            CITATION_COVERAGE.set(round(max(0, total_claims - check.unverified_claims) / total_claims, 4))

        # 落库助手消息（含引用/冲突/降级标注）
        from core.guardrails.masking import mask_text_for_role
        from core.orchestration.session import ChatSessionStore

        store = ChatSessionStore(self.db)
        # BR-08 三处口径一致：冲突元数据与正文同规则、同敏感字段集合脱敏
        meta_sensitive: list[str] = sorted({f for e in state.evidence for f in (e.meta or {}).get("sensitive_fields", [])})
        conflict_summaries = []
        for c in state.conflicts:
            summary = {
                "conflict_id": c.conflict_id,
                "title": c.title,
                "internal": c.internal_claim.statement,
                "external": c.external_claim.statement,
                "must_note": c.expected_resolution.get("must_note", ""),
                "status": c.status,
            }
            for key in ("title", "internal", "external", "must_note"):
                if summary.get(key):
                    summary[key], _ = mask_text_for_role(str(summary[key]), state.role_id, meta_sensitive)
            conflict_summaries.append(summary)
        message = store.add_message(
            state.session_id,
            "assistant",
            check.text,
            citations=state.draft.citations,
            conflicts=conflict_summaries,
            eval_ref=(state.eval_result or {}).get("eval_id"),
            degrade_level=str(state.degrade_level),
            trace_id=state.turn_id,
            meta={
                "annotations": state.draft.annotations,
                "steps": [t.to_dict() for t in state.trace],
                "demo_mode": self._demo_mode(),
                "override_flags": state.draft.override_flags,
            },
        )
        state.assistant_message_id = message.message_id

        # 过程帧先行（citation），正文 token 校验后开流（决策 D11）
        for item in state.evidence:
            await emit(
                "citation",
                {
                    "cid": item.citation_id,
                    "chunk_id": item.chunk_id,
                    "source_type": item.source_type,
                    "source_ref": item.source_ref,
                    "confidence": item.confidence,
                    "record_id": (item.meta or {}).get("record_id", ""),
                },
            )
        for start in range(0, len(check.text), 24):
            await emit("token", {"text": check.text[start : start + 24]})

    def _demo_mode(self) -> str:
        from app.config import get_settings

        return get_settings().DEMO_MODE

    def new_assistant_message_id(self) -> str:
        return f"MSG-{uuid.uuid4().hex[:16]}"
