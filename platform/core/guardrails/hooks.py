"""五类安全 Hook 的公共类型与审计留痕（技术方案 03 §3.6；FR-6-05）。

HookContext / HookDecision 为五类 Hook 的统一契约；全部决策携 trace_id 落 gov_audit_log。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor, AuditEntry
from core.abstraction.enums import HookAction
from core.runtime import audit_repo


@dataclass
class HookContext:
    stage: str
    actor: Actor | None = None
    domain_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class HookDecision:
    action: str = HookAction.PASS
    payload: dict[str, Any] = field(default_factory=dict)  # mask 后的新载荷
    code: str = ""
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # confirm 卡片信息 / 标注等

    @property
    def passed(self) -> bool:
        return self.action == HookAction.PASS

    @property
    def blocked(self) -> bool:
        return self.action == HookAction.BLOCK


def write_hook_audit(
    db: Session,
    ctx: HookContext,
    decision: HookDecision,
    *,
    action: str,
    detail: dict[str, Any] | None = None,
    resource: str | None = None,
) -> None:
    """五类 Hook 决策全量落审计（携 trace_id，只追加）。"""
    from app.common.context import get_trace_id
    from core.metrics import HOOK_DECISIONS

    HOOK_DECISIONS.labels(stage=ctx.stage, decision=str(decision.action)).inc()
    audit_repo(db).append(
        AuditEntry(
            trace_id=ctx.trace_id or get_trace_id(),
            actor_id=ctx.actor.user_id if ctx.actor else None,
            action=action,
            resource=resource or ctx.domain_id,
            hook_type=ctx.stage,
            decision=str(decision.action),
            detail=detail or {},
        )
    )
