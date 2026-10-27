"""政务域安全辅助：输入注入检查与审计留痕。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.context import get_trace_id
from app.common.errors import BusinessError
from core.abstraction.dtos import Actor, AuditEntry
from core.guardrails.input_hook import detect_injection
from core.runtime import audit_repo


def ensure_safe_text(*texts: str) -> None:
    """拒绝明确的提示注入载荷；正文证据仍由输出/导出检查单独处理。"""
    for text in texts:
        hits = detect_injection(text or "")
        if hits:
            raise BusinessError(
                "INJECTION_DETECTED",
                f"输入包含不安全内容（{'、'.join(hits)}），已拦截",
                400,
            )


def append_gov_audit(
    db: Session,
    actor: Actor,
    *,
    action: str,
    resource: str,
    detail: dict[str, Any],
    trace_id: str = "",
) -> None:
    audit_repo(db).append(
        AuditEntry(
            trace_id=trace_id or get_trace_id(),
            actor_id=actor.user_id,
            action=action,
            resource=resource,
            hook_type="gov",
            decision="PASS",
            detail=detail,
        )
    )
