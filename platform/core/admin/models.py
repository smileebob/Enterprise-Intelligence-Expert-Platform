"""管理域（M9）ORM 模型：用户/角色/权限/领域实例。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
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
    """领域实例装配配置（版本化）。"""

    __tablename__ = "dom_instance"

    domain_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/online/offline
    config: Mapped[dict] = mapped_column(json_col(), default=dict)  # domain.yaml 定稿
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    config_history: Mapped[list] = mapped_column(json_col(), default=list)  # 回滚用（上一版本）
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
