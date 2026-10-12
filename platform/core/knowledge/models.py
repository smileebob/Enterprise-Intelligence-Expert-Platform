"""知识域 ORM 模型（物理表对照 04-数据架构 §4.1/§4.2）。

- kb_document / kb_chunk（向量+全文+六级分级+引用锚点）/ kb_entity / kb_relation / kb_conflict
- kb_ingest_task：入库任务状态（API 轮询进度用）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import TsVectorType, VectorType, json_col


class KbDocument(Base):
    __tablename__ = "kb_document"

    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    modality: Mapped[str] = mapped_column(String(32), default="structured_json")
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    permission_level: Mapped[int] = mapped_column(Integer, default=1)
    origin: Mapped[str] = mapped_column(String(256), default="")
    version: Mapped[str] = mapped_column(String(32), default="v1")
    collected_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class KbChunk(Base):
    __tablename__ = "kb_chunk"

    chunk_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kb_document.doc_id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[str] = mapped_column(Text, default="")  # jieba 预分词（空格分隔）
    content_tsv: Mapped[str | None] = mapped_column(TsVectorType(), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(VectorType(1024), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(64), default="")
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    permission_level: Mapped[int] = mapped_column(Integer, default=1)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    record_id: Mapped[str] = mapped_column(String(64), default="", index=True)  # 领域记录 ID 锚点（如 EXP-2201 / EXT-STD-006）
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class KbEntity(Base):
    __tablename__ = "kb_entity"

    entity_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    attributes: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class KbRelation(Base):
    __tablename__ = "kb_relation"

    relation_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    from_entity: Mapped[str] = mapped_column(String(160), index=True)
    to_entity: Mapped[str] = mapped_column(String(160), index=True)
    rel_type: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_chunk: Mapped[str | None] = mapped_column(String(160), nullable=True)


class KbConflict(Base):
    __tablename__ = "kb_conflict"

    conflict_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    topic: Mapped[str] = mapped_column(String(128), default="")
    conflict_type: Mapped[str] = mapped_column(String(128), default="")
    internal_claim: Mapped[dict] = mapped_column(json_col(), default=dict)
    external_claim: Mapped[dict] = mapped_column(json_col(), default=dict)
    expected_resolution: Mapped[dict] = mapped_column(json_col(), default=dict)
    demo_query: Mapped[str] = mapped_column(Text, default="")
    trap: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    resolution: Mapped[dict | None] = mapped_column(json_col(), nullable=True)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class KbIngestTask(Base):
    __tablename__ = "kb_ingest_task"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(64), index=True)
    source_name: Mapped[str] = mapped_column(String(256), default="")
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="parsing", index=True)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    docs: Mapped[int] = mapped_column(Integer, default=0)
    conflicts_detected: Mapped[int] = mapped_column(Integer, default=0)
    pending_review: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
