"""② 检索 Hook：权限过滤与分区参数注入 —— 过滤下推 SQL、无权限数据不出库（03 §3.6）。

安全定版（附录A D8）：分区策略与权限过滤由服务端决定，客户端不可指定。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor
from core.abstraction.enums import HookAction
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit


def derive_permission_max(actor: Actor | None, requested: int | None = None) -> int:
    """有效数据权限 = min(角色数据权限, 调用方请求)；服务端裁决，客户端不可越权。"""
    actor_max = actor.permission_max if actor else 1
    if requested is None:
        return actor_max
    return min(actor_max, requested)


def apply_retrieval_guard(
    db: Session,
    actor: Actor | None,
    domain_id: str,
    query: str,
    *,
    permission_requested: int | None = None,
    session_id: str = "",
    trace_id: str = "",
) -> int:
    """注入 permission_max 并留痕（每次检索一条审计）。返回生效的 permission_max。"""
    effective = derive_permission_max(actor, permission_requested)
    ctx = HookContext(
        stage="retrieval",
        actor=actor,
        domain_id=domain_id,
        session_id=session_id,
        payload={"query": query},
        trace_id=trace_id,
    )
    decision = HookDecision(
        action=HookAction.PASS,
        payload={"permission_max": effective, "query": query},
        extra={"partitions": ["internal", "external"]},
    )
    write_hook_audit(
        db,
        ctx,
        decision,
        action="hook.retrieval.pass",
        detail={"permission_max": effective, "query_len": len(query or "")},
    )
    return effective
