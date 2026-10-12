"""化学领域插件 · 结构化业务表（对照 04-领域数据模型 §4.1 与 07 数据集 JSON schema）。

说明：06 数据集的性能/原料字段内嵌于产品记录（performance/tech_params），
MVP 不单列 chem_performance/chem_material 表；结构感知切分时全字段入知识块。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class ChemProduct(Base):
    __tablename__ = "chem_product"

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(64), default="")
    sub_type: Mapped[str] = mapped_column(String(128), default="")
    tech_params: Mapped[dict] = mapped_column(json_col(), default=dict)
    performance: Mapped[dict] = mapped_column(json_col(), default=dict)
    applicable_fabric: Mapped[list] = mapped_column(json_col(), default=list)
    process_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compliance: Mapped[str] = mapped_column(Text, default="")
    compliance_level: Mapped[str] = mapped_column(String(32), default="ok")  # clean/ok/restricted_substance
    notes: Mapped[str] = mapped_column(Text, default="")
    data_source: Mapped[str] = mapped_column(String(256), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="internal")
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)


class ChemFormula(Base):
    __tablename__ = "chem_formula"

    formula_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    composition: Mapped[list] = mapped_column(json_col(), default=list)
    pickup: Mapped[str] = mapped_column(String(32), default="")
    cure: Mapped[str] = mapped_column(String(128), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    confidential: Mapped[bool] = mapped_column(default=True)
    source_type: Mapped[str] = mapped_column(String(16), default="internal")
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)


class ChemProcess(Base):
    __tablename__ = "chem_process"

    process_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    method: Mapped[str] = mapped_column(String(64), default="")
    conditions: Mapped[dict] = mapped_column(json_col(), default=dict)
    applicable_fabric: Mapped[list] = mapped_column(json_col(), default=list)
    equipment: Mapped[str] = mapped_column(String(256), default="")
    key_controls: Mapped[list] = mapped_column(json_col(), default=list)
    common_issues: Mapped[list] = mapped_column(json_col(), default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(16), default="internal")
    source_ref: Mapped[str] = mapped_column(String(256), default="")


class ChemExperiment(Base):
    __tablename__ = "chem_experiment"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    fabric: Mapped[str] = mapped_column(String(256), default="")
    product_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    formula_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    process: Mapped[str] = mapped_column(Text, default="")
    results: Mapped[dict] = mapped_column(json_col(), default=dict)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    customer: Mapped[str] = mapped_column(String(256), default="")
    tested_at: Mapped[str] = mapped_column(String(32), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="internal")
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    conflict_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class ChemCustomerNeed(Base):
    __tablename__ = "chem_customer_need"

    need_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(64), default="")
    customer_need: Mapped[str] = mapped_column(Text, default="")
    fabric_type: Mapped[str] = mapped_column(String(128), default="")
    target_performance: Mapped[dict] = mapped_column(json_col(), default=dict)
    process_conditions: Mapped[dict] = mapped_column(json_col(), default=dict)
    market: Mapped[str] = mapped_column(String(64), default="")
    clarify_items: Mapped[list] = mapped_column(json_col(), default=list)
    expected_answer_points: Mapped[list] = mapped_column(json_col(), default=list)
    should_reference: Mapped[list] = mapped_column(json_col(), default=list)
    conflict_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), default="stated")
    source_ref: Mapped[str] = mapped_column(String(256), default="")


class ChemSolution(Base):
    """智能体生成的方案（评估对象 SOLUTION）。"""

    __tablename__ = "chem_solution"

    solution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    need_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario: Mapped[str] = mapped_column(String(64), default="默认")
    recommended_products: Mapped[list] = mapped_column(json_col(), default=list)
    recommended_process: Mapped[dict] = mapped_column(json_col(), default=dict)
    validation_directions: Mapped[list] = mapped_column(json_col(), default=list)
    evidence: Mapped[list] = mapped_column(json_col(), default=list)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    feedback: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
