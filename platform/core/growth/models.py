"""自成长域 ORM 模型：反馈样本与纠错规则。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class GrowthFeedback(Base):
    __tablename__ = "growth_feedback"

    feedback_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    eval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    rating: Mapped[str] = mapped_column(String(16), index=True)  # adopted/modified/rejected
    correction: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sample_type: Mapped[str] = mapped_column(String(16), default="")  # positive/negative/correction
    case_doc_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 正样本回流案例库
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True
    )


class GrowthCorrectionRule(Base):
    """负样本提炼的纠错规则（注入编排 System 负向约束）。"""

    __tablename__ = "growth_correction_rule"

    rule_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_text: Mapped[str] = mapped_column(Text)
    rule_type: Mapped[str] = mapped_column(String(32), default="text_only")  # text_only/forbid_claim/add_verification
    target_fact: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(64), default="")
    source_feedback: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
