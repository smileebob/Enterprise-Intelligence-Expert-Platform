"""编排域 ORM 模型：会话/消息/检查点（interrupt 恢复）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class ChatSession(Base):
    __tablename__ = "chat_session"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(24), default="active")  # active/interrupted/closed
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_session.session_id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list | None] = mapped_column(json_col(), nullable=True)
    conflicts: Mapped[list | None] = mapped_column(json_col(), nullable=True)
    eval_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    degrade_level: Mapped[str] = mapped_column(String(8), default="L0")
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)  # adopted/modified/rejected
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True
    )


class ChatSessionSummary(Base):
    """会话摘要（04 §4）：确定性槽位摘要，下一轮真实消费；CAS 覆盖避免丢新消息。"""

    __tablename__ = "chat_session_summary"

    summary_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(default=1)
    covered_message_ids: Mapped[list] = mapped_column(json_col(), default=list)
    structured_slots: Mapped[dict] = mapped_column(json_col(), default=dict)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="deterministic")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class AgentCheckpoint(Base):
    """编排检查点：interrupt/resume 与断点续跑（PostgresSaver 同构，落业务库）。"""

    __tablename__ = "agent_checkpoint"

    checkpoint_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    node: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="running")  # running/interrupted/done/failed
    interrupt_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)  # clarify/tool_confirm
    state: Mapped[dict] = mapped_column(json_col(), default=dict)  # 序列化 State（不含向量）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
