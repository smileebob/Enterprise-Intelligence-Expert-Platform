"""评估域 ORM 模型（物理表对照 04-数据架构 §4.2：eval_standard + eval_result）。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class EvalStandard(Base):
    __tablename__ = "eval_standard"

    standard_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    year: Mapped[int] = mapped_column(Integer)
    object_type: Mapped[str] = mapped_column(String(32))
    sub_domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    standard_desc: Mapped[str] = mapped_column(Text, default="")
    dimension_weights: Mapped[dict] = mapped_column(json_col(), default=dict)
    scoring_rubric: Mapped[dict] = mapped_column(json_col(), default=dict)
    scenario_profiles: Mapped[dict] = mapped_column(json_col(), default=dict)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(48), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # draft/active/archived
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class EvalResult(Base):
    __tablename__ = "eval_result"

    eval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str] = mapped_column(String(32), default="solution")
    object_ref: Mapped[dict] = mapped_column(json_col(), default=dict)
    standard_id: Mapped[str] = mapped_column(String(96), index=True)
    scenario: Mapped[str] = mapped_column(String(64))
    dimension_scores: Mapped[dict] = mapped_column(json_col(), default=dict)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    calibration: Mapped[dict | None] = mapped_column(json_col(), nullable=True)
    risk_flags: Mapped[list] = mapped_column(json_col(), default=list)
    compliance_flags: Mapped[list] = mapped_column(json_col(), default=list)
    guardrails: Mapped[list] = mapped_column(json_col(), default=list)
    scored_by: Mapped[str] = mapped_column(String(32), default="rule+llm")
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirm_action: Mapped[dict | None] = mapped_column(json_col(), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True
    )
