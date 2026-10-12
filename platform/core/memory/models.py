"""记忆域 ORM 模型（物理表对照 04-数据架构 §4.2：mem_item + mem_event）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import VectorType, json_col


class MemItem(Base):
    __tablename__ = "mem_item"

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    layer: Mapped[str] = mapped_column(String(16), index=True)  # session/user/enterprise/domain
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    permission_scope: Mapped[str] = mapped_column(String(16), default="owner")  # owner/team/domain/all
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(1024), nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), default="ai_inferred")
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.6"))
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promotion_basis: Mapped[str | None] = mapped_column(String(256), nullable=True)
    derived_from: Mapped[list | None] = mapped_column(json_col(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class MemEvent(Base):
    """记忆全量变更事件（apply/promote/decay/archive/merge/update/delete），审计可回放。"""

    __tablename__ = "mem_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(24), index=True)
    detail: Mapped[dict] = mapped_column(json_col(), default=dict)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True
    )
