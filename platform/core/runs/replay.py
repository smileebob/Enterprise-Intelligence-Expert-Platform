"""frozen 回放（05-B 切片）：节点快照 + 生成/输出纯计算，零业务写入。"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.payload_store import PayloadIntegrityError, PayloadUnavailable
from core.gateway.permissions import role_permissions
from core.runs.models import AgentRunEvent, RunPayload
from core.runs.service import RunAccessDenied, RunService


class ReplayUnavailable(Exception):
    """缺少完整快照/载荷已删除/运行不可回放。"""


class ReplayVersionMismatch(Exception):
    """本机代码版本与录制时不一致。"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()  # noqa: S324


class FrozenReplayService:
    def __init__(self, db: Session):
        self.db = db
        self.runs = RunService(db)

    def _check_access(self, run, actor) -> None:
        self.runs._check_view(run, actor)
        if actor.user_id == run.owner_id:
            return
        if "run.replay" not in role_permissions(self.db, actor.role_id, fresh=True):
            raise RunAccessDenied("无权回放该运行")

    def _recorded_output(self, run_id: str) -> tuple[str, str]:
        event = (
            self.db.execute(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run_id, AgentRunEvent.event_type == "output.ready")
                .order_by(AgentRunEvent.seq.desc())
            )
            .scalars()
            .first()
        )
        if event is None or not event.payload_ref:
            raise ReplayUnavailable("缺少 output.ready 记录载荷")
        payload = self.db.get(RunPayload, event.payload_ref)
        if payload is None or payload.deletion_state != "available":
            raise ReplayUnavailable("输出载荷已删除或过期")
        try:
            data = self.runs.store.load_json(event.payload_ref)
        except (PayloadUnavailable, PayloadIntegrityError) as exc:
            raise ReplayUnavailable("输出载荷不可读") from exc
        text = str(data.get("text") or "")
        return text, str((event.metadata_json or {}).get("text_digest") or "")

    async def replay(self, run_id: str, actor) -> dict[str, Any]:
        run = self.runs.get_run(run_id)
        self._check_access(run, actor)
        if run.status != "completed":
            raise ReplayUnavailable(f"运行状态为 {run.status}，不可 frozen 回放")
        checkpoint = self.runs.latest_checkpoint(run_id, next_node="generate") or self.runs.latest_checkpoint(run_id)
        if checkpoint is None:
            raise ReplayUnavailable("缺少节点快照")
        expected_code = str((run.manifest or {}).get("code_version") or "")
        from core.domains import load_domain_hooks

        current_code = getattr(load_domain_hooks(run.domain_id), "CODE_VERSION", "") or ""
        if expected_code and current_code and expected_code != current_code:
            raise ReplayVersionMismatch("本机代码版本与录制快照不一致")

        try:
            state_data = self.runs.load_checkpoint_state(checkpoint)
        except (PayloadUnavailable, PayloadIntegrityError) as exc:
            raise ReplayUnavailable("快照载荷不可读（已删除或损坏）") from exc
        recorded_text, recorded_digest = self._recorded_output(run_id)

        from core.orchestration.engine import Orchestrator
        from core.orchestration.state import TurnState

        orchestrator = Orchestrator(self.db)
        orchestrator.replay_mode = True
        state = TurnState.from_dict(state_data)
        state.run_id = ""  # 禁止写运行事件
        plugin = orchestrator.registry.require(state.domain_id)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, data: dict[str, Any]) -> None:
            emitted.append((kind, data))

        if checkpoint.next_node == "generate" or state.draft is None:
            await orchestrator._node_generate(state, plugin, actor, emit)
        await orchestrator._node_output(state, plugin, actor, emit)

        capture = orchestrator.replay_capture
        replay_text = str(capture.get("text") or state.plan.get("final_text") or "")
        replay_digest = _digest(replay_text)
        first_diff = next(
            (
                index
                for index, (left, right) in enumerate(zip(recorded_text, replay_text, strict=False))
                if left != right
            ),
            min(len(recorded_text), len(replay_text)),
        )
        return {
            "run_id": run_id,
            "replay_kind": "frozen_generate_output",
            "checkpoint_id": checkpoint.checkpoint_id,
            "checkpoint_after_seq": checkpoint.after_seq,
            "checkpoint_kind": checkpoint.checkpoint_kind,
            "recorded_text_digest": recorded_digest,
            "replay_text_digest": replay_digest,
            "recorded_length": len(recorded_text),
            "replay_length": len(replay_text),
            "first_diff_index": first_diff,
            "text_match": replay_text == recorded_text,
            "blocked": bool(capture.get("blocked")),
            "action": capture.get("action", ""),
            "token_events": sum(1 for kind, _ in emitted if kind == "token"),
            "no_business_writes": True,
            "limitations": [
                "仅重算 generate/output；检索/模型/工具使用快照，不发起外部调用",
                "插件配置读取当前生效版本；代码版本不一致时拒绝回放",
            ],
        }
