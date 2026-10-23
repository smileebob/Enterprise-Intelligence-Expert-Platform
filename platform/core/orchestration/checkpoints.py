"""编排检查点：中断恢复 / 断点续跑 / 审计三合一（PostgresSaver 同构，落业务库）。"""

from __future__ import annotations

from sqlalchemy import select, update
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


def claim_latest_interrupted(db: Session, session_id: str) -> tuple[int, TurnState] | None:
    """CAS 认领：interrupted → running；两个并发 resume 只有一个成功。"""
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
    state_data = dict(row.state or {})
    checkpoint_id = row.checkpoint_id
    result = db.execute(
        update(AgentCheckpoint)
        .where(AgentCheckpoint.checkpoint_id == checkpoint_id, AgentCheckpoint.status == "interrupted")
        .values(status="running")
    )
    if getattr(result, "rowcount", 0) != 1:
        db.rollback()
        return None
    db.commit()
    return checkpoint_id, TurnState.from_dict(state_data)


def mark_status(db: Session, checkpoint_id: int, status: str) -> None:
    db.execute(
        update(AgentCheckpoint)
        .where(AgentCheckpoint.checkpoint_id == checkpoint_id)
        .values(status=status)
    )
    db.commit()


def mark_done(db: Session, checkpoint_id: int) -> None:
    mark_status(db, checkpoint_id, "done")


def recent_running_traces(db: Session, session_id: str, limit: int = 5) -> list[AgentCheckpoint]:
    rows = (
        db.execute(
            select(AgentCheckpoint)
            .where(AgentCheckpoint.session_id == session_id)
            .order_by(AgentCheckpoint.checkpoint_id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return list(rows)
