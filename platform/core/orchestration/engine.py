"""智能体编排引擎（M2，技术方案 03 §3.2）：状态图节点 + 中断/恢复 + 检查点 + 重规划 N=2。

流程：输入(路由层)→澄清→规划→检索→融合→工具→评估→生成→自检→(重规划)→输出。
中断点：澄清追问 / 危险工具确认；检查点落业务库（断点续跑/审计）。
"""

from __future__ import annotations

import hashlib
import json
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
    has_capability,
)
from core.orchestration.models import ChatSession
from core.orchestration.need_extractor import (
    extract_need,
    infer_scenario,
    merge_history_context,
    merge_resume_answer,
    required_slots,
)
from core.orchestration.state import StepTrace, TurnState

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]

_DEGRADE_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def _digest_json(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()  # noqa: S324


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
        from core.runs.recording import RecordingModelGateway, RunContext
        from core.runtime import get_model_gateway

        self.db = db
        self.run_ctx = RunContext()
        self.gateway = RecordingModelGateway(gateway or get_model_gateway(), db, self.run_ctx)
        self.registry: Any
        if registry is not None:
            self.registry = registry
        else:
            # 正式链路以 DB 生效 revision 为权威；单测可显式注入文件注册表
            from core.runtime import get_db_domain_registry

            self.registry = get_db_domain_registry(db)
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
        run_id: str = "",
    ) -> TurnOutcome:
        plugin = self.registry.require(session.domain_id)
        from app.common.context import get_trace_id

        trace_id = get_trace_id()
        if not trace_id or trace_id == "-":
            trace_id = f"T-{uuid.uuid4().hex[:12]}"
        state = TurnState(
            session_id=session.session_id,
            turn_id=trace_id,
            run_id=run_id,
            user_id=actor.user_id,
            role_id=actor.role_id,
            domain_id=session.domain_id,
            message_id=message_id,
            user_msg=user_msg,
        )
        state.history = self._load_history(session.session_id, exclude_message_id=message_id)
        self.run_ctx.run_id = run_id
        self.run_ctx.owner_id = actor.user_id
        self.run_ctx.domain_id = session.domain_id
        try:
            return await self._pipeline(state, plugin, actor, emit, start="clarify")
        finally:
            self.run_ctx.run_id = ""

    def _load_history(self, session_id: str, *, exclude_message_id: str = "") -> list[dict[str, str]]:
        """装载会话滑窗（最近 N 条消息，时序升序；剔除本轮消息）。单条截断以控检查点体积。"""
        from app.config import get_settings
        from core.orchestration.session import ChatSessionStore

        window = max(int(get_settings().ORCH_HISTORY_WINDOW), 0)
        if window <= 0:
            return []
        rows = ChatSessionStore(self.db).recent_messages(session_id, limit=window)
        from core.orchestration.summary import SessionSummaryService

        summary = SessionSummaryService(self.db).get(session_id)
        covered = set(summary.covered_message_ids or []) if summary is not None else set()
        messages = [
            {"role": m.role, "content": (m.content or "")[:500]}
            for m in rows
            if m.message_id != exclude_message_id
            and m.message_id not in covered
            and (m.content or "").strip()
        ]
        if summary is not None and summary.summary_text:
            messages.insert(0, {"role": "system", "content": summary.summary_text})
        from core.orchestration.context import trim_history

        return trim_history(messages, max(1024, int(get_settings().ORCH_HISTORY_MAX_BYTES)))

    def _raise_degrade(self, state: TurnState, level: str) -> None:
        """按严重度上浮回合降级层级（只升不降）。"""
        if _DEGRADE_RANK.get(level, 0) > _DEGRADE_RANK.get(str(state.degrade_level), 0):
            state.degrade_level = level

    async def resume(
        self,
        session_id: str,
        resume_value: Any,
        actor: Actor,
        *,
        emit: EmitFn | None = None,
        run_id: str = "",
        approval_id: str = "",
        review_id: str = "",
    ) -> TurnOutcome:
        # 原子认领：两个并发 resume 只有一个能把 interrupted 推进到 running
        loaded = checkpoints.claim_latest_interrupted(self.db, session_id)
        if loaded is None:
            raise KeyError("没有待恢复的中断对话")
        checkpoint_id, state = loaded
        plugin = self.registry.require(state.domain_id)
        start = "plan"
        if state.interrupt_kind == "output_review":
            if not review_id:
                checkpoints.mark_status(self.db, checkpoint_id, "failed_recoverable")
                raise ValueError("输出审核未批准")
            from core.runs.output_reviews import OutputReviewService, draft_digest

            try:
                review = OutputReviewService(self.db).consume(
                    review_id, draft_digest(state.draft), actor
                )
            except Exception:
                self._run_status(state, "cancelled", event_type="run.cancelled")
                checkpoints.mark_status(self.db, checkpoint_id, "failed_recoverable")
                raise
            state.plan["output_review_approval"] = review.review_id
            start = "output"
        state.interrupted = False
        state.resume_value = resume_value
        if run_id:
            state.run_id = run_id
        if approval_id:
            state.plan["approval_id"] = approval_id
        if not state.history:
            state.history = self._load_history(state.session_id)
        if state.interrupt_kind == "clarify" and state.need:
            state.need = merge_resume_answer(state.need, str(resume_value or ""), plugin)
        self.run_ctx.run_id = state.run_id
        self.run_ctx.owner_id = actor.user_id
        self.run_ctx.domain_id = state.domain_id
        try:
            if state.run_id:
                from core.runs.service import RunService

                service = RunService(self.db)
                try:
                    service.set_status(state.run_id, "running", event_type="run.resumed")
                except KeyError:  # pragma: no cover - 兼容不存在的老运行
                    pass
            # 从规划继续；输出审核批准后只重放 output 节点
            outcome = await self._pipeline(state, plugin, actor, emit, start=start, checkpoint_id=checkpoint_id)
            checkpoints.mark_status(self.db, checkpoint_id, "superseded" if outcome.interrupted else "done")
            return outcome
        except Exception:
            checkpoints.mark_status(self.db, checkpoint_id, "failed_recoverable")
            raise
        finally:
            self.run_ctx.run_id = ""

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
                self._run_event(state, "node.started", node=node, metadata={"summary": f"{node} 开始"})
                try:
                    await handler(state, plugin, actor, emit_event)
                except Exception as exc:
                    self._run_event(
                        state, "node.failed", node=node, metadata={"error_type": type(exc).__name__}
                    )
                    raise
                else:
                    self._run_event(state, "node.completed", node=node, metadata={"summary": f"{node} 完成"})
                finally:
                    AGENT_NODE_DURATION.labels(node=node).observe(_time.perf_counter() - node_started)
                if state.interrupted:
                    checkpoint_id = checkpoints.save_checkpoint(
                        self.db, state, node, status="interrupted", interrupt_kind=state.interrupt_kind
                    )
                    if state.interrupt_kind == "tool_confirm":
                        self._run_status(state, "waiting_approval", event_type="run.waiting_approval")
                    elif state.interrupt_kind == "output_review":
                        self._run_status(state, "waiting_review", event_type="run.waiting_review")
                    else:
                        self._run_status(state, "waiting_input", event_type="run.waiting_input")
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
        except Exception:
            self._run_status(state, "failed", event_type="run.failed")
            raise

        self._run_status(state, "completed", event_type="run.completed")

        final_text = str(state.plan.get("final_text", ""))
        DEGRADE_LEVEL.labels(level=str(state.degrade_level)).inc()
        await emit_event(
            "done",
            {
                "code": "SUCCESS",
                "data": {
                    "run_id": state.run_id,
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
        # 04 §4：摘要中的定位槽位先补缺失值（早于历史滑窗，避免摘要被窗口截掉后丢失）
        from core.orchestration.summary import SessionSummaryService

        summary_inherited = SessionSummaryService(self.db).apply_to_need(state.session_id, need)
        if summary_inherited:
            state.plan["summary_inherited"] = summary_inherited
            state.trace.append(
                StepTrace(node="context", status="done", summary="已从会话摘要补齐定位槽位")
            )
        if need.market != "unknown" and need.scenario == "默认":
            need.scenario = infer_scenario(need.market, need.raw_text)
        # 多轮指代消解：摘要槽位 + 会话滑窗共同补齐缺失定位槽位
        inherited: dict[str, str] = dict(summary_inherited)
        if state.history:
            inherited.update(
                {
                    key: value
                    for key, value in merge_history_context(need, state.history, plugin).items()
                    if key not in inherited
                }
            )
        if inherited:
            state.plan["inherited_slots"] = inherited
            note = "；".join(f"{k}={v}" for k, v in inherited.items() if not k.startswith("__"))
            summary = f"已继承会话上下文槽位（{note or '市场=' + inherited.get('__market__', '')}）"
            state.trace.append(StepTrace(node="context", status="done", summary=summary))
            await emit("step", {"node": "context", "status": "done", "summary": summary})
        self._run_event(
            state,
            "need.extracted",
            node="clarify",
            metadata={
                "scenario": need.scenario,
                "market": need.market,
                "mode": need.slots.get("__mode__", ""),
                "slots": len(need.slots),
                "summary": f"需求解析：场景={need.scenario}，市场={need.market}",
            },
            payload=need.to_dict(),
            classification="sensitive",
        )
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

    # ---------------- 领域钩子（回合快照固定，不随运行中配置切换） ----------------
    def _hooks(self, plugin: DomainPlugin) -> Any:
        return getattr(plugin, "hooks", None)

    def _snapshot(self, plugin: DomainPlugin, state: TurnState) -> PluginSnapshot:
        existing = state.plan.get("plugin_snapshot")
        if isinstance(existing, dict) and existing.get("domain_id") == plugin.domain_id:
            return PluginSnapshot(**existing)
        hooks = self._hooks(plugin)
        snapshot = PluginSnapshot(
            domain_id=plugin.domain_id,
            version=plugin.version,
            code_version=getattr(hooks, "CODE_VERSION", "") if hooks is not None else "",
        )
        state.plan["plugin_snapshot"] = {
            "domain_id": snapshot.domain_id,
            "version": snapshot.version,
            "code_version": snapshot.code_version,
            "config_rev": snapshot.config_rev,
        }
        return snapshot

    def _run_event(
        self,
        state: TurnState,
        event_type: str,
        *,
        node: str = "",
        metadata: dict[str, Any] | None = None,
        payload: Any | None = None,
        classification: str = "internal",
        call_id: str = "",
    ) -> None:
        if not state.run_id:
            return
        from core.runs.service import RunService

        RunService(self.db).append_event(
            state.run_id,
            event_type,
            node=node,
            metadata=metadata,
            payload=payload,
            classification=classification,
            call_id=call_id,
        )

    def _run_status(self, state: TurnState, status: str, event_type: str | None = None) -> None:
        if not state.run_id:
            return
        from core.runs.service import RunService

        try:
            RunService(self.db).set_status(state.run_id, status, event_type=event_type)
        except KeyError:  # pragma: no cover - 兼容不存在的老运行
            logger.warning("run %s not found when setting %s", state.run_id, status)

    @staticmethod
    def _need_view(need) -> NeedView:
        if need is None:
            return NeedView()
        return NeedView(
            scenario=need.scenario,
            market=need.market,
            mode=str(need.slots.get("__mode__", "")),
            slots=dict(need.slots),
            target_performance=tuple(need.target_performance),
            product_ids=tuple(need.product_ids),
            user_msg=need.raw_text,
        )

    # ---------------- 节点：规划（钩子只产意图，不读取证据） ----------------
    async def _node_plan(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        need = state.need
        hooks = self._hooks(plugin)
        intents: list[ToolIntent] = []
        if need is not None and has_capability(hooks, "plan_tools"):
            intents = hooks.plan_tools(self._need_view(need), self._snapshot(plugin, state))
        planned_tools = [intent.tool_name for intent in intents]
        state.plan.update(
            {
                "tools": planned_tools,
                "tool_intents": [intent.to_dict() for intent in intents],
                "evaluate": bool(need and need.evaluate),
                "scenario": need.scenario if need else "默认",
            }
        )
        # 推理自成长：仅加载已人工批准的结构化规则（原文反馈不直接成为指令）
        if "active_rules" not in state.plan:
            try:
                from core.growth.service import GrowthService

                state.plan["active_rules"] = GrowthService(self.db).active_rules(state.domain_id, limit=10)
            except Exception:
                logger.debug("load constraint rules failed", exc_info=True)
                state.plan["active_rules"] = []
        self._run_event(
            state,
            "constraints.loaded",
            node="plan",
            metadata={
                "rule_ids": [rule.get("rule_id") for rule in state.plan.get("active_rules", [])],
                "summary": f"已批准纠错规则 {len(state.plan.get('active_rules', []))} 条",
            },
        )
        self._run_event(
            state,
            "plan.created",
            node="plan",
            metadata={
                "tools": planned_tools,
                "evaluate": bool(state.plan.get("evaluate")),
                "scenario": state.plan.get("scenario", ""),
                "summary": f"工具规划 {len(planned_tools)} 个；评估={'是' if state.plan.get('evaluate') else '否'}",
            },
        )
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
        self._run_event(
            state,
            "retrieval.completed",
            node="retrieve",
            metadata={
                "evidence_count": len(fused.evidence),
                "partition_stats": fused.partition_stats,
                "citations": [
                    {
                        "citation_id": item.citation_id,
                        "chunk_id": item.chunk_id,
                        "source_type": item.source_type,
                        "record_id": (item.meta or {}).get("record_id", ""),
                    }
                    for item in fused.evidence
                ],
                "summary": f"检索 {len(fused.evidence)} 条证据",
            },
            payload={
                "need": need.to_dict() if need else None,
                "evidence": [
                    {
                        "chunk_id": item.chunk_id,
                        "content": item.content,
                        "source_type": item.source_type,
                        "source_ref": item.source_ref,
                        "confidence": item.confidence,
                        "citation_id": item.citation_id,
                        "meta": item.meta,
                    }
                    for item in fused.evidence
                ],
            },
            classification="sensitive",
        )
        self._run_event(
            state,
            "memory.recalled",
            node="retrieve",
            metadata={
                "count": len(memory_items),
                "memory_ids": [item.memory_id for item in memory_items],
                "summary": f"记忆注入 {len(memory_items)} 条",
            },
            payload={"items": state.memory_items},
            classification="sensitive",
        )

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
        self._run_event(
            state,
            "fusion.completed",
            node="fuse",
            metadata={
                "conflict_ids": [conflict.conflict_id for conflict in fused.conflicts],
                "override_flags": fused.override_flags,
                "summary": fuse_summary,
            },
        )

    # ---------------- 节点：工具调用（经网关 Hook③；参数在检索后由钩子绑定） ----------------
    async def _node_tools(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        intents = [ToolIntent.from_dict(item) for item in state.plan.get("tool_intents", [])]
        tool_results: list[dict[str, Any]] = []
        if not intents:
            state.plan["tool_results"] = tool_results
            return
        hooks = self._hooks(plugin)
        snapshot = self._snapshot(plugin, state)
        ctx = ToolCtx(
            db=self.db,
            actor=actor,
            session_id=state.session_id,
            domain_id=state.domain_id,
            trace_id=state.turn_id,
            run_id=state.run_id,
            node="tool",
            approval_id=str(state.plan.get("approval_id", "")),
            gateway=self.gateway,
            registry=self.registry,
            extra={"market": state.need.market if state.need else ""},
        )
        evidence_view = EvidenceView(need=self._need_view(state.need), evidence=tuple(state.evidence))
        for intent in intents:
            tool_name = intent.tool_name
            call_id = f"{state.turn_id}:tool:{intent.invocation_index}"
            ctx.invocation_index = intent.invocation_index
            try:
                args = dict(intent.static_args)
                if has_capability(hooks, "bind_tool_args"):
                    args.update(hooks.bind_tool_args(intent, evidence_view, snapshot))
                self._run_event(
                    state,
                    "tool.requested",
                    node="tool",
                    call_id=call_id,
                    metadata={
                        "tool": tool_name,
                        "invocation_index": intent.invocation_index,
                        "args_digest": _digest_json(args),
                        "summary": f"调用工具 {tool_name}",
                    },
                    payload={"tool": tool_name, "args": args},
                    classification="sensitive",
                )
                result = await self.tools.invoke(tool_name, args, ctx)
            except Exception as exc:
                self._run_event(
                    state,
                    "tool.failed",
                    node="tool",
                    call_id=call_id,
                    metadata={
                        "tool": tool_name,
                        "error_type": type(exc).__name__,
                        "summary": f"工具 {tool_name} 失败",
                    },
                )
                logger.warning("tool %s failed, continue pipeline", tool_name, exc_info=True)
                continue
            # 04：超大工具结果落受控载荷，本轮只消费有界投影
            from app.config import get_settings
            from core.orchestration.context import project_tool_data
            from core.runs.service import RunService, canonical_json

            data_for_state = result.data
            tool_meta = dict(result.meta or {})
            if state.run_id and result.data is not None:
                projected, projection = project_tool_data(
                    result.data, inline_bytes=get_settings().TOOL_OUTPUT_INLINE_BYTES
                )
                if projection["truncated"]:
                    service = RunService(self.db)
                    payload_row = service.store.put(
                        canonical_json(result.data).encode("utf-8"),
                        owner_id=actor.user_id,
                        domain_id=state.domain_id,
                        classification="internal",
                        sources=[("tool", tool_name)],
                        retention_days=get_settings().RUN_PAYLOAD_RETENTION_DAYS,
                    )
                    self.db.commit()
                    data_for_state = projected
                    tool_meta.update(
                        {
                            "truncated": True,
                            "offloaded_payload_ref": payload_row.opaque_id,
                            "original_bytes": projection["original_bytes"],
                            "total_count": projection["total_count"],
                        }
                    )
                    self._run_event(
                        state,
                        "context.offloaded",
                        node="tool",
                        call_id=call_id,
                        metadata={
                            "tool": tool_name,
                            "payload_ref": payload_row.opaque_id,
                            "original_bytes": projection["original_bytes"],
                            "summary": "工具结果超限，已受控卸载",
                        },
                    )
            tool_results.append({"tool": tool_name, "ok": result.ok, "data": data_for_state, "meta": tool_meta})
            self._run_event(
                state,
                "tool.returned",
                node="tool",
                call_id=call_id,
                metadata={
                    "tool": tool_name,
                    "ok": bool(result.ok),
                    "source_type": tool_meta.get("source_type", ""),
                    "result_count": len(data_for_state) if isinstance(data_for_state, list) else None,
                    "truncated": bool(tool_meta.get("truncated")),
                    "offloaded_payload_ref": tool_meta.get("offloaded_payload_ref"),
                    "summary": f"工具 {tool_name} 返回（ok={bool(result.ok)}）",
                },
                payload={"tool": tool_name, "ok": result.ok, "data": data_for_state, "meta": tool_meta},
                classification="sensitive",
            )
            if (result.meta or {}).get("confirm_required"):
                approval_id = str(result.meta.get("approval_id", ""))
                state.interrupted = True
                state.interrupt_kind = "tool_confirm"
                state.interrupt_payload = {
                    "approval_id": approval_id,
                    "tool": tool_name,
                    "effect_key": result.meta.get("effect_key", ""),
                    "card": result.meta.get("card", {}),
                    "question": f"工具 {tool_name} 需要一次性审批后执行。",
                }
                self._run_event(
                    state,
                    "approval.requested",
                    node="tool",
                    call_id=call_id,
                    metadata={
                        "approval_id": approval_id,
                        "tool": tool_name,
                        "effect_key": result.meta.get("effect_key", ""),
                        "summary": f"等待审批：{tool_name}",
                    },
                    payload={"card": result.meta.get("card", {}), "message": result.message},
                )
                await emit(
                    "clarify",
                    {
                        "kind": "tool_confirm",
                        **state.interrupt_payload,
                    },
                )
                break
            # 工具结果 → 引用补齐（领域钩子只产 refs，补证走受控 KnowledgeService 门面）
            if has_capability(hooks, "evidence_refs_from_result"):
                result_view = ToolResultView(
                    tool_name=tool_name,
                    ok=bool(result.ok),
                    data=result.data,
                    meta=dict(result.meta or {}),
                )
                refs = hooks.evidence_refs_from_result(intent, result_view, snapshot)
                self._augment_evidence_from_refs(state, refs, actor)
        state.plan["tool_results"] = tool_results
        summary = f"工具调用 {len(tool_results)} 次（经权限网关与审计）"
        state.trace.append(StepTrace(node="tool", status="done", summary=summary))
        await emit("step", {"node": "tool", "status": "done", "summary": summary})

    def _augment_evidence_from_refs(self, state: TurnState, refs: list[str], actor: Actor) -> None:
        """引用补齐：域/权限/ready 受控；供方案装配与输出校验使用。"""
        record_ids = [str(ref) for ref in refs if ref]
        if not record_ids:
            return
        existing = {e.chunk_id for e in state.evidence}
        fetched = self.knowledge.authorized_chunks_by_refs(record_ids, state.domain_id, actor)
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

    # ---------------- 节点：六维评估（可选；装配/持久化经领域钩子） ----------------
    async def _node_evaluate(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        need = state.need
        if not need or not state.plan.get("evaluate"):
            return
        hooks = self._hooks(plugin)
        snapshot = self._snapshot(plugin, state)
        spec: EvalRequestSpec | None = None
        if has_capability(hooks, "assemble_evaluation"):
            turn = TurnView(
                session_id=state.session_id,
                turn_id=state.turn_id,
                domain_id=state.domain_id,
                need=self._need_view(need),
                evidence=tuple(state.evidence),
                tool_results=tuple(
                    ToolResultView(
                        tool_name=str(item.get("tool", "")),
                        ok=bool(item.get("ok")),
                        data=item.get("data"),
                        meta=dict(item.get("meta") or {}),
                    )
                    for item in state.plan.get("tool_results", [])
                ),
            )
            spec = hooks.assemble_evaluation(turn, snapshot)
        if spec is None or not spec.object_ref:
            state.plan["evaluate"] = False
            return
        if has_capability(hooks, "persist_outcome"):
            from core.runs.effects import EffectLedger

            execution = ExecutionContext(
                session_id=state.session_id,
                run_id=state.turn_id,
                message_id=state.message_id,
                domain_id=state.domain_id,
            )
            effect_key = f"{state.run_id or state.turn_id}:evaluate:0"
            ledger = EffectLedger(self.db)
            effect = ledger.prepare(
                effect_key,
                run_id=state.run_id or state.turn_id,
                node="evaluate",
                operation=spec.object_type,
                request_digest=_digest_json(spec.object_ref),
            )
            if effect.status == "succeeded" and effect.result_ref:
                spec.object_ref["solution_id"] = effect.result_ref
            else:
                spec = hooks.persist_outcome(self.db, execution, spec, snapshot)
                ledger.mark(effect, "succeeded", result_ref=str(spec.object_ref.get("solution_id", "")))
            self.db.commit()  # 领域钩子只 flush；事务边界由调用方控制
        state.plan["object_ref"] = spec.object_ref
        from core.evaluation.engine import EvaluationEngine

        engine = EvaluationEngine(self.db, gateway=self.gateway, registry=self.registry)
        try:
            eval_result = await engine.evaluate(
                {
                    "domain_id": state.domain_id,
                    "object_type": spec.object_type,
                    "object_ref": spec.object_ref,
                    "scenario": spec.scenario,
                    "need_ref": spec.need_ref,
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
        self._run_event(
            state,
            "evaluation.completed",
            node="evaluate",
            metadata={
                "eval_id": eval_result.get("eval_id"),
                "total_score": eval_result.get("total_score"),
                "confidence": eval_result.get("confidence"),
                "compliance_flags": eval_result.get("compliance_flags", []),
                "degrade_level": eval_result.get("degrade_level"),
                "summary": f"六维评估 {eval_result.get('total_score')}",
            },
            payload=eval_result,
        )
        # 降级层级贯穿：live 配置下校准落 L1/L2（模型失败/熔断）时上浮回合 meta（06 §6.5 诚实徽标）；
        # offline/replay 为确定性离线通道（其通道性质由界面「离线模式」标注表达），不上浮
        from adapters.model_gateway import ResilientGateway

        if isinstance(getattr(self.gateway, "delegate", self.gateway), ResilientGateway):
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

    # ---------------- 节点：生成（领域 composer 经钩子调用） ----------------
    async def _node_generate(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        hooks = self._hooks(plugin)
        if not has_capability(hooks, "compose"):
            raise RuntimeError(f"领域 {state.domain_id} 未提供 compose 能力")
        fused = FusedEvidence(
            evidence=state.evidence,
            conflicts=state.conflicts,
            partition_stats={},
            override_flags=state.plan.get("override_flags", []),
            notes=state.plan.get("fuse_notes", []),
        )
        draft = hooks.compose(
            GenerationContext(
                need=state.need,
                fused=fused,
                eval_result=state.eval_result,
                tool_results=tuple(state.plan.get("tool_results", [])),
                degrade_level=state.degrade_level,
            ),
            self._snapshot(plugin, state),
        )
        # 结构化纠错规则确定性应用（06-B：返回 applied_ids，实际影响正文才计数）
        from core.growth.constraints import apply_constraint_rules
        from core.growth.service import GrowthService

        applied_ids, constraint_effects = apply_constraint_rules(
            draft, list(state.plan.get("active_rules", []))
        )
        if applied_ids:
            try:
                GrowthService(self.db).mark_rules_used(applied_ids)
            except Exception:
                logger.warning("mark constraint rules used failed", exc_info=True)
        # 记忆复用说明（非事实结论，不占引用）
        if state.memory_items:
            draft.annotations.append(f"已结合您的历史偏好与组织沉淀（记忆复用 {len(state.memory_items)} 条）")
        inherited = state.plan.get("inherited_slots") or {}
        if isinstance(inherited, dict) and inherited:
            note = "；".join(f"{k}={v}" for k, v in inherited.items() if not k.startswith("__"))
            if not note:
                note = f"目标市场={inherited.get('__market__', '')}"
            draft.annotations.append(f"已结合本会话上下文（继承：{note}）")
        state.draft = draft
        self._run_event(
            state,
            "draft.created",
            node="generate",
            metadata={
                "claims": len(draft.all_claims()),
                "citations": len(draft.citations),
                "annotations": len(draft.annotations),
                "applied_constraint_ids": applied_ids,
                "constraint_effects": constraint_effects,
                "degrade_level": draft.degrade_level,
                "summary": f"草稿生成（{len(draft.all_claims())} 条结论）",
            },
            payload={
                "summary": draft.summary,
                "sections": [section.to_dict() for section in draft.sections],
                "citations": draft.citations,
                "annotations": draft.annotations,
                "confidence": draft.confidence,
            },
            classification="sensitive",
        )
        summary = f"方案草稿生成（{len(draft.all_claims())} 条结论，全部挂引用）"
        state.trace.append(StepTrace(node="generate", status="done", summary=summary))
        await emit("step", {"node": "generate", "status": "done", "summary": summary})

    # ---------------- 自检与重规划（N=2，03 §3.2 实现要点 4） ----------------
    async def _self_check_loop(self, state: TurnState, plugin: DomainPlugin, actor: Actor, emit: EmitFn) -> None:
        from app.config import get_settings

        max_replan = get_settings().ORCH_MAX_REPLAN
        draft = state.draft
        if draft is None:
            raise RuntimeError("自检阶段缺少草稿")
        while True:
            check = self.output_guard.validate_citations_only(draft, state.evidence)
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
                draft.annotations.append(annotation)
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
        draft = state.draft
        if draft is None:
            raise RuntimeError("输出阶段缺少草稿")
        hook_ctx = HookContext(
            stage="output",
            actor=actor,
            domain_id=state.domain_id,
            session_id=state.session_id,
            payload={"claims": len(draft.all_claims())},
            trace_id=state.turn_id,
        )
        check = self.output_guard.check(
            draft,
            state.evidence,
            hook_ctx,
            confidence_threshold=plugin.confidence_threshold,
        )
        self.db.commit()
        draft.annotations = list(dict.fromkeys(check.annotations + draft.annotations))
        state.plan["final_text"] = check.text
        # 引用合规底线指标：带引用结论占比（06 §6.6，告警基线 <0.95）
        from core.metrics import CITATION_COVERAGE

        total_claims = len(draft.all_claims())
        if total_claims:
            CITATION_COVERAGE.set(round(max(0, total_claims - check.unverified_claims) / total_claims, 4))

        # 06-C：review/block 必须在任何正文 token 之前停下，等待人工审核
        approved_review = str(state.plan.get("output_review_approval") or "")
        if check.action in ("review", "block") and not approved_review:
            from core.runs.output_reviews import OutputReviewService

            review = OutputReviewService(self.db).create(
                run_id=state.run_id or state.turn_id,
                session_id=state.session_id,
                owner_id=state.user_id,
                domain_id=state.domain_id,
                draft=draft,
                action=check.action,
                reasons=check.review_reasons or check.annotations,
                policy_version=check.policy_version,
            )
            state.interrupted = True
            state.interrupt_kind = "output_review"
            state.interrupt_payload = {
                "review_id": review.review_id,
                "action": check.action,
                "reasons": check.review_reasons,
                "question": "输出触发人工审核，批准前不会发送正文。",
            }
            self._run_event(
                state,
                "review.decided",
                node="output",
                metadata={
                    "review_id": review.review_id,
                    "action": check.action,
                    "status": "pending",
                    "reasons": check.review_reasons,
                    "summary": "输出待人工审核",
                },
            )
            await emit("clarify", {"kind": "output_review", **state.interrupt_payload})
            return
        if approved_review:
            draft.annotations.append(f"已通过人工审核（{approved_review}）")

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
            citations=draft.citations,
            conflicts=conflict_summaries,
            eval_ref=(state.eval_result or {}).get("eval_id"),
            degrade_level=str(state.degrade_level),
            trace_id=state.turn_id,
            meta={
                "annotations": draft.annotations,
                "steps": [t.to_dict() for t in state.trace],
                "demo_mode": self._demo_mode(),
                "override_flags": draft.override_flags,
                "applied_constraint_ids": draft.applied_constraint_ids,
                "constraint_effects": draft.constraint_effects,
            },
        )
        state.assistant_message_id = message.message_id
        try:
            from core.orchestration.summary import SessionSummaryService

            SessionSummaryService(self.db).record_turn(
                session_id=state.session_id,
                owner_id=state.user_id,
                domain_id=state.domain_id,
                need=state.need,
                user_text=state.user_msg,
                user_message_id=state.message_id,
                assistant_message_id=message.message_id,
            )
        except Exception:
            logger.warning("session summary update failed", exc_info=True)
        self._run_event(
            state,
            "output.ready",
            node="output",
            metadata={
                "message_id": message.message_id,
                "text_digest": _digest_json(check.text),
                "citations": len(draft.citations),
                "unverified_claims": check.unverified_claims,
                "summary": "输出审核通过，正文可发送",
            },
            payload={"text": check.text, "annotations": draft.annotations},
            classification="sensitive",
        )

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
