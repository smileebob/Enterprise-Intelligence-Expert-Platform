"""派发 outbox（05-B）：效果派发与业务事务同提交；重复 enqueue 幂等。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.runs.models import AgentRunOutbox


class OutboxService:
    def __init__(self, db: Session):
        self.db = db

    def enqueue(
        self,
        *,
        run_id: str,
        effect_key: str,
        operation: str,
        payload_ref: str | None = None,
    ) -> AgentRunOutbox:
        existing = (
            self.db.execute(select(AgentRunOutbox).where(AgentRunOutbox.effect_key == effect_key))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        row = AgentRunOutbox(
            outbox_id=f"OB-{uuid.uuid4().hex[:16]}",
            run_id=run_id,
            effect_key=effect_key,
            operation=operation,
            payload_ref=payload_ref,
            status="pending",
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            return row
        except IntegrityError:
            existing = (
                self.db.execute(select(AgentRunOutbox).where(AgentRunOutbox.effect_key == effect_key))
                .scalars()
                .first()
            )
            if existing is None:
                raise
            return existing

    def pending(self, limit: int = 50) -> list[AgentRunOutbox]:
        return list(
            self.db.execute(
                select(AgentRunOutbox)
                .where(AgentRunOutbox.status == "pending")
                .order_by(AgentRunOutbox.created_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
