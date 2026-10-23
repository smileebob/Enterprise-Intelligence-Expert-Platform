"""危险工具一次性审批（06-A2）：申请即绑定 effect/参数/工具/权限版本。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from core.gateway.permissions import role_permissions
from core.runs.models import ExecutionApproval
from core.runs.service import canonical_json


class ApprovalNotFound(Exception):
    pass


class ApprovalConflict(Exception):
    pass


class ApprovalExpired(Exception):
    pass


def args_digest(args: dict) -> str:
    return hashlib.sha256(canonical_json(args).encode("utf-8")).hexdigest()  # noqa: S324


def permission_version(db: Session, role_id: str) -> str:
    perms = role_permissions(db, role_id, fresh=True)
    return hashlib.sha256(",".join(sorted(perms)).encode("utf-8")).hexdigest()[:16]  # noqa: S324


def _now() -> datetime:
    return datetime.now(UTC)


class ApprovalService:
    def __init__(self, db: Session):
        self.db = db

    def create_or_get(
        self,
        *,
        run_id: str,
        owner_id: str,
        domain_id: str,
        session_id: str,
        effect_key: str,
        tool_name: str,
        tool_version: str,
        args: dict,
        preview: dict,
        actor_role: str,
    ) -> ExecutionApproval:
        existing = (
            self.db.execute(select(ExecutionApproval).where(ExecutionApproval.effect_key == effect_key))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        approval = ExecutionApproval(
            approval_id=f"APV-{uuid.uuid4().hex[:16]}",
            owner_id=owner_id,
            domain_id=domain_id,
            session_id=session_id,
            run_id=run_id,
            effect_key=effect_key,
            tool_name=tool_name,
            tool_version=tool_version,
            args_digest=args_digest(args),
            permission_version=permission_version(self.db, actor_role),
            status="pending",
            preview=preview,
            expires_at=_now() + timedelta(seconds=get_settings().APPROVAL_TTL_SECONDS),
        )
        self.db.add(approval)
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            existing = (
                self.db.execute(
                    select(ExecutionApproval).where(ExecutionApproval.effect_key == effect_key)
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return existing
            raise
        return approval

    def decide(self, approval_id: str, action: str, actor) -> ExecutionApproval:
        approval = self.db.get(ExecutionApproval, approval_id)
        if approval is None:
            raise ApprovalNotFound("审批不存在")
        if approval.domain_id not in (actor.domain_scope or []):
            raise ApprovalNotFound("审批不存在")
        if approval.status != "pending":
            raise ApprovalConflict(f"审批状态为 {approval.status}，不可再次决策")
        self._ensure_not_expired(approval)
        status = "approved" if action == "approve" else "rejected"
        result = self.db.execute(
            update(ExecutionApproval)
            .where(ExecutionApproval.approval_id == approval_id, ExecutionApproval.status == "pending")
            .values(status=status, decided_by=actor.user_id, decided_at=_now())
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise ApprovalConflict("审批已被其他操作处理")
        self.db.commit()
        self.db.refresh(approval)
        return approval

    def consume(
        self,
        approval_id: str,
        *,
        effect_key: str,
        tool_name: str,
        args: dict,
        actor,
    ) -> ExecutionApproval:
        """CAS approved→consumed；调用方必须把 effect dispatched 与本消费放在同一事务提交。"""
        approval = self.db.get(ExecutionApproval, approval_id)
        if approval is None or approval.domain_id not in (actor.domain_scope or []):
            raise ApprovalNotFound("审批不存在")
        if approval.effect_key != effect_key:
            raise ApprovalConflict("审批与当前 effect 不匹配")
        if approval.tool_name != tool_name:
            raise ApprovalConflict("审批与当前工具不匹配")
        if approval.args_digest != args_digest(args):
            raise ApprovalConflict("审批与当前参数不匹配")
        if approval.status != "approved":
            raise ApprovalConflict(f"审批状态为 {approval.status}，不可执行")
        self._ensure_not_expired(approval)
        result = self.db.execute(
            update(ExecutionApproval)
            .where(ExecutionApproval.approval_id == approval_id, ExecutionApproval.status == "approved")
            .values(status="consumed")
        )
        if getattr(result, "rowcount", 0) != 1:
            self.db.rollback()
            raise ApprovalConflict("审批已被消费")
        self.db.refresh(approval)
        return approval

    def list_pending(self, domain_id: str, limit: int = 50) -> list[ExecutionApproval]:
        rows = (
            self.db.execute(
                select(ExecutionApproval)
                .where(ExecutionApproval.domain_id == domain_id, ExecutionApproval.status == "pending")
                .order_by(ExecutionApproval.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def view(self, approval_id: str, actor) -> ExecutionApproval:
        approval = self.db.get(ExecutionApproval, approval_id)
        if approval is None or approval.domain_id not in (actor.domain_scope or []):
            raise ApprovalNotFound("审批不存在")
        return approval

    def _ensure_not_expired(self, approval: ExecutionApproval) -> None:
        expires_at = approval.expires_at
        if expires_at is None:
            return
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= _now():
            approval.status = "expired"
            self.db.commit()
            raise ApprovalExpired("审批已过期")
