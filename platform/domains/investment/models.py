"""招商领域插件 · 结构化业务表（对照 04-领域数据模型 §4.2，轻量 MVP）。

缓建说明（技术方案 01 §1.7）：funding/patent/paper/lead 表 MVP 不建，
企业画像所需聚合字段内嵌于 inv_profile。
"""

from __future__ import annotations

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.db_types import json_col


class InvEnterprise(Base):
    __tablename__ = "inv_enterprise"

    ent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    aliases: Mapped[list] = mapped_column(json_col(), default=list)
    track: Mapped[str] = mapped_column(String(64), default="")  # 创新药/基因细胞/AI制药/器械/医美
    stage: Mapped[str] = mapped_column(String(64), default="")
    region: Mapped[str] = mapped_column(String(128), default="")
    unified_credit_code: Mapped[str] = mapped_column(String(64), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="external")
    source_ref: Mapped[str] = mapped_column(String(256), default="")


class InvProfile(Base):
    __tablename__ = "inv_profile"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ent_id: Mapped[str] = mapped_column(String(64), index=True)
    tech_direction: Mapped[dict] = mapped_column(json_col(), default=dict)
    funding_stage: Mapped[str] = mapped_column(String(64), default="")
    rd_needs: Mapped[dict] = mapped_column(json_col(), default=dict)
    expansion_signals: Mapped[dict] = mapped_column(json_col(), default=dict)
    six_dim: Mapped[dict] = mapped_column(json_col(), default=dict)
    data_completeness: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    source_type: Mapped[str] = mapped_column(String(16), default="external")
    source_ref: Mapped[str] = mapped_column(String(256), default="")


class InvParkResource(Base):
    __tablename__ = "inv_park_resource"

    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), default="")  # 空间/政策/产业配套
    name: Mapped[str] = mapped_column(String(256), default="")
    attributes: Mapped[dict] = mapped_column(json_col(), default=dict)
    availability: Mapped[str] = mapped_column(String(64), default="available")


class InvPolicy(Base):
    __tablename__ = "inv_policy"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    applicability: Mapped[dict] = mapped_column(json_col(), default=dict)
    issuing_body: Mapped[str] = mapped_column(String(256), default="")
    valid_until: Mapped[str] = mapped_column(String(32), default="")
    source_type: Mapped[str] = mapped_column(String(16), default="external")
    source_ref: Mapped[str] = mapped_column(String(256), default="")
