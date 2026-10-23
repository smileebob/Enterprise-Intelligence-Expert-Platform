"""副作用账本（05-B）：effect_key 唯一，prepared/dispatched/succeeded/failed/unknown。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.runs.models import AgentRunEffect


class EffectLedger:
    def __init__(self, db: Session):
        self.db = db

    def get(self, effect_key: str) -> AgentRunEffect | None:
        return self.db.get(AgentRunEffect, effect_key)

    def prepare(
        self,
        effect_key: str,
        *,
        run_id: str,
        node: str,
        invocation_index: int = 0,
        operation: str,
        request_digest: str = "",
        approval_id: str | None = None,
    ) -> AgentRunEffect:
        """幂等领取：已存在直接返回（不因重试再执行一次本地写）。"""
        existing = self.get(effect_key)
        if existing is not None:
            return existing
        effect = AgentRunEffect(
            effect_key=effect_key,
            run_id=run_id,
            node=node,
            invocation_index=invocation_index,
            operation=operation,
            request_digest=request_digest,
            status="prepared",
            approval_id=approval_id,
        )
        try:
            with self.db.begin_nested():
                self.db.add(effect)
                self.db.flush()
            return effect
        except Exception:
            existing = self.get(effect_key)
            if existing is not None:
                return existing
            raise

    def mark(
        self,
        effect: AgentRunEffect,
        status: str,
        *,
        result_ref: str | None = None,
        external_idempotency_key: str | None = None,
    ) -> AgentRunEffect:
        effect.status = status
        if result_ref is not None:
            effect.result_ref = result_ref
        if external_idempotency_key is not None:
            effect.external_idempotency_key = external_idempotency_key
        self.db.flush()
        return effect
