"""编排检查点：中断恢复 / 断点续跑 / 审计三合一（PostgresSaver 同构，落业务库）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.orchestration.models import AgentCheckpoint
from core.orchestration.state import TurnState


def save_checkpoint(
    db: Session,
    state: TurnState,
    node: str,
    *,
    status: str = "running",
    interrupt_kind: str | None = None,
) -> int:
    checkpoint = AgentCheckpoint(
        session_id=state.session_id,
        turn_id=state.turn_id,
        node=node,
        status=status,
        interrupt_kind=interrupt_kind or state.interrupt_kind or None,
        state=state.to_dict(),
    )
    db.add(checkpoint)
    db.flush()
    db.commit()
    return checkpoint.checkpoint_id


def load_latest_interrupted(db: Session, session_id: str) -> tuple[int, TurnState] | None:
    row = (
        db.execute(
            select(AgentCheckpoint)
            .where(AgentCheckpoint.session_id == session_id, AgentCheckpoint.status == "interrupted")
            .order_by(AgentCheckpoint.checkpoint_id.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return row.checkpoint_id, TurnState.from_dict(row.state or {})


def mark_done(db: Session, checkpoint_id: int) -> None:
    row = db.get(AgentCheckpoint, checkpoint_id)
    if row is not None:
        row.status = "done"
        db.commit()


def recent_running_traces(db: Session, session_id: str, limit: int = 5) -> list[AgentCheckpoint]:
    return (
        db.execute(
            select(AgentCheckpoint)
            .where(AgentCheckpoint.session_id == session_id)
            .order_by(AgentCheckpoint.checkpoint_id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
