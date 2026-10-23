"""管理域（M9）ORM 模型：用户/角色/权限/领域实例。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class GovRole(Base):
    __tablename__ = "gov_role"

    role_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")


class GovPermission(Base):
    __tablename__ = "gov_permission"

    perm_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(16), default="all")  # owner/team/domain/all
    description: Mapped[str] = mapped_column(Text, default="")


class GovRolePermission(Base):
    __tablename__ = "gov_role_permission"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(48), index=True)
    perm_id: Mapped[str] = mapped_column(String(64), index=True)


class GovUser(Base):
    __tablename__ = "gov_user"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(64), default="")
    org: Mapped[str] = mapped_column(String(128), default="")
    role_id: Mapped[str] = mapped_column(String(48), index=True)
    domain_scope: Mapped[list] = mapped_column(json_col(), default=lambda: ["chemical"])
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class DomInstance(Base):
    """领域实例装配配置（revision 指针 + 兼容快照字段）。"""

    __tablename__ = "dom_instance"

    domain_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/online/offline
    config: Mapped[dict] = mapped_column(json_col(), default=dict)  # 当前生效配置快照（兼容/展示）
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    config_rev: Mapped[int] = mapped_column(default=0, server_default="0")  # 全域单调变更号（CAS 版本）
    active_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_revision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_history: Mapped[list] = mapped_column(json_col(), default=list)  # 旧字段保留（只读兼容）
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class DomRevision(Base):
    """不可变配置修订（草稿/发布/回滚/下线都产生新修订，不原地修改）。"""

    __tablename__ = "dom_revision"
    __table_args__ = (UniqueConstraint("domain_id", "config_rev", name="uq_dom_revision_domain_rev"),)

    revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    config_rev: Mapped[int] = mapped_column(index=True)
    config: Mapped[dict] = mapped_column(json_col(), default=dict)
    code_version: Mapped[str] = mapped_column(String(64), default="")
    schema_version: Mapped[str] = mapped_column(String(16), default="1")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    change_kind: Mapped[str] = mapped_column(String(32), default="draft")  # draft/publish/rollback/offline
    restored_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class DomOperation(Base):
    """管理写操作幂等记录（主体 + 操作 + 资源 + 幂等键唯一）。"""

    __tablename__ = "dom_operation"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "domain_id", "idem_key", name="uq_dom_operation_key"),
    )

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(32))
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    idem_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    response: Mapped[dict | None] = mapped_column(json_col(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
