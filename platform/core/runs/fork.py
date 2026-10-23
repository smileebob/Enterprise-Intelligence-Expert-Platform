"""live_eval 分叉（05-C 切片）：新 run + 白名单 patch + 只读工具 + 零业务写入。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from adapters.payload_store import PayloadIntegrityError, PayloadUnavailable
from core.gateway.permissions import role_permissions
from core.runs.models import AgentRunCheckpoint
from core.runs.service import RunAccessDenied, RunService

logger = logging.getLogger(__name__)


class ForkUnavailable(Exception):
    """缺少可复制的快照或运行不可分叉。"""


class ForkInputRejected(Exception):
    def __init__(self, message: str, code: str = "VALIDATION_ERROR"):
        self.code = code
        super().__init__(message)


class LiveEvalForkService:
    def __init__(self, db: Session):
        self.db = db
        self.runs = RunService(db)

    def _check_access(self, run, actor) -> None:
        self.runs._check_view(run, actor)
        if actor.user_id == run.owner_id:
            return
        if "run.fork" not in role_permissions(self.db, actor.role_id, fresh=True):
            raise RunAccessDenied("无权分叉该运行")

    def _checkpoint(self, run_id: str, checkpoint_id: str | None) -> AgentRunCheckpoint:
        if checkpoint_id:
            checkpoint = self.db.get(AgentRunCheckpoint, checkpoint_id)
            if checkpoint is None or checkpoint.run_id != run_id:
                raise ForkUnavailable("指定的检查点不属于该运行")
            return checkpoint
        checkpoint = self.runs.latest_checkpoint(run_id, next_node="generate") or self.runs.latest_checkpoint(run_id)
        if checkpoint is None:
            raise ForkUnavailable("缺少可复制的节点快照")
        return checkpoint

    async def fork(self, run_id: str, actor, *, user_msg: str, checkpoint_id: str | None = None) -> dict[str, Any]:
        source = self.runs.get_run(run_id)
        self._check_access(source, actor)
        if source.status != "completed":
            raise ForkUnavailable(f"源运行状态为 {source.status}，不可分叉")
        checkpoint = self._checkpoint(run_id, checkpoint_id)
        try:
            state_data = self.runs.load_checkpoint_state(checkpoint)
        except (PayloadUnavailable, PayloadIntegrityError) as exc:
            raise ForkUnavailable("快照载荷不可读") from exc

        from core.guardrails.hooks import HookContext
        from core.guardrails.input_hook import InputGuard

        ctx = HookContext(
            stage="input",
            actor=actor,
            domain_id=source.domain_id,
            session_id=f"fork:{source.run_id}",
        )
        decision = InputGuard(self.db).check(user_msg, ctx)
        if decision.blocked:
            raise ForkInputRejected(decision.message, decision.code or "VALIDATION_ERROR")
        text = decision.payload.get("text", user_msg)

        child = self.runs.create_run(
            session_id=f"FORK-{uuid.uuid4().hex[:12]}",
            owner_id=actor.user_id,
            domain_id=source.domain_id,
            mode="live_eval",
            manifest={
                **(source.manifest or {}),
                "parent_run_id": source.run_id,
                "fork_checkpoint_id": checkpoint.checkpoint_id,
                "fork_mode": "live_eval",
            },
        )
        child.parent_run_id = source.run_id
        child.fork_checkpoint_id = checkpoint.checkpoint_id
        self.db.commit()

        from core.orchestration.engine import Orchestrator
        from core.orchestration.state import TurnState

        orchestrator = Orchestrator(self.db)
        orchestrator.write_suppressed = True
        orchestrator.run_ctx.run_id = child.run_id
        orchestrator.run_ctx.owner_id = actor.user_id
        orchestrator.run_ctx.domain_id = source.domain_id
        state = TurnState.from_dict(state_data)
        state.session_id = child.session_id
        state.run_id = child.run_id
        state.user_id = actor.user_id
        state.role_id = actor.role_id
        state.user_msg = text
        state.message_id = ""
        state.history = []
        state.need = None
        state.evidence = []
        state.conflicts = []
        state.draft = None
        state.eval_result = None
        state.plan = {}
        state.trace = []
        state.interrupted = False
        state.interrupt_kind = ""
        state.interrupt_payload = {}
        state.correction_rules = []
        plugin = orchestrator.registry.require(source.domain_id)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, data: dict[str, Any]) -> None:
            emitted.append((kind, data))

        try:
            outcome = await orchestrator._pipeline(state, plugin, actor, emit, start="clarify")
        except Exception:
            try:
                self.runs.finish_run(child.run_id, "failed")
            except Exception:
                logger.warning("mark forked run failed", exc_info=True)
            raise
        self.db.expire_all()
        child = self.runs.get_run(child.run_id)
        return {
            "run_id": child.run_id,
            "parent_run_id": source.run_id,
            "fork_checkpoint_id": checkpoint.checkpoint_id,
            "mode": "live_eval",
            "status": child.status,
            "interrupted": bool(outcome.interrupted),
            "output_text": str((orchestrator.replay_capture or {}).get("text") or ""),
            "token_events": sum(1 for kind, _ in emitted if kind == "token"),
            "no_business_writes": True,
            "data_model_drift": True,
            "limitations": [
                "live_eval 使用当前数据/模型，非确定性复现",
                "只读工具沙箱；不继承父运行的危险批准",
                "分叉输出只在试验 run 中，不写入对话/方案/评估表",
            ],
        }
