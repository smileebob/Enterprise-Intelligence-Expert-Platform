"""治理域 ORM 模型：工具注册表与审计日志（只追加）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class GovTool(Base):
    __tablename__ = "gov_tool"

    tool_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(32), default="retrieval")
    risk_level: Mapped[str] = mapped_column(String(16), default="normal")
    required_perm: Mapped[str] = mapped_column(String(64), default="")
    domain_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    input_schema: Mapped[dict] = mapped_column(json_col(), default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)


class GovAuditLog(Base):
    """审计日志：五类 Hook 决策与工具调用全量留痕，只追加（04-数据架构 DDL）。"""

    __tablename__ = "gov_audit_log"

    log_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str | None] = mapped_column(String(256), nullable=True)
    hook_type: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[dict] = mapped_column(json_col(), default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True
    )
