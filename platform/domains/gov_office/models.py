"""政务办公领域 ORM 模型：政策文件、版本、条款、关系和导入幂等记录。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from core.clock import utc_now
from core.db_types import json_col


class GovPolicyDocument(Base):
    __tablename__ = "gov_policy_document"

    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    document_number: Mapped[str] = mapped_column(String(128), default="", index=True)
    issuing_authority: Mapped[str] = mapped_column(String(256))
    jurisdiction: Mapped[str] = mapped_column(String(128), index=True)
    policy_level: Mapped[str] = mapped_column(String(64), default="")
    knowledge_status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    source_type: Mapped[str] = mapped_column(String(16), default="external", index=True)
    permission_level: Mapped[int] = mapped_column(Integer, default=1)
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class GovPolicyVersion(Base):
    __tablename__ = "gov_policy_version"
    __table_args__ = (
        UniqueConstraint("doc_id", "version_label", name="uq_gov_policy_version_label"),
    )

    version_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("gov_policy_document.doc_id"), index=True)
    version_label: Mapped[str] = mapped_column(String(96))
    document_number: Mapped[str] = mapped_column(String(128), default="", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    source_retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    source_hash: Mapped[str] = mapped_column(String(64))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class GovPolicyClause(Base):
    __tablename__ = "gov_policy_clause"
    __table_args__ = (
        UniqueConstraint("version_id", "clause_no", name="uq_gov_policy_clause_no"),
    )

    clause_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("gov_policy_version.version_id"), index=True)
    clause_no: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(512), default="")
    content: Mapped[str] = mapped_column(Text)
    conditions: Mapped[dict] = mapped_column(json_col(), default=dict)
    exceptions: Mapped[dict] = mapped_column(json_col(), default=dict)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(json_col(), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class GovPolicyRelation(Base):
    __tablename__ = "gov_policy_relation"

    relation_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    from_version_id: Mapped[str] = mapped_column(String(192), index=True)
    to_version_id: Mapped[str] = mapped_column(String(192), index=True)
    relation_type: Mapped[str] = mapped_column(String(32), index=True)
    basis: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class GovInterpretSession(Base):
    __tablename__ = "gov_interpret_session"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    jurisdiction: Mapped[str] = mapped_column(String(128), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="clarify", index=True)
    turns: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    last_result: Mapped[dict | None] = mapped_column(json_col(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class GovInterpretOperation(Base):
    __tablename__ = "gov_interpret_operation"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(json_col(), default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class GovWritingDraft(Base):
    __tablename__ = "gov_writing_draft"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    request: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(256), default="")
    jurisdiction: Mapped[str] = mapped_column(String(128), default="")
    subject: Mapped[str] = mapped_column(String(256), default="")
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    materials: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    outline: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    policy_citations: Mapped[list[str]] = mapped_column(json_col(), default=list)
    result: Mapped[dict] = mapped_column(json_col(), default=dict)
    checks: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class GovDocumentDraft(Base):
    __tablename__ = "gov_document_draft"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    recipient: Mapped[str] = mapped_column(String(256), default="")
    issuing_authority: Mapped[str] = mapped_column(String(256))
    document_date: Mapped[date] = mapped_column(Date)
    purpose: Mapped[str] = mapped_column(Text)
    sections: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    facts: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    policy_citations: Mapped[list[str]] = mapped_column(json_col(), default=list)
    checks: Mapped[list[dict]] = mapped_column(json_col(), default=list)
    template_id: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class GovPolicyImportOperation(Base):
    __tablename__ = "gov_policy_import_operation"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    doc_id: Mapped[str] = mapped_column(String(128), index=True)
    version_id: Mapped[str] = mapped_column(String(192))
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
