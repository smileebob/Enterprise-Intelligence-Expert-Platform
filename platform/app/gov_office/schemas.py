"""政务政策接口契约。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DOC_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$"
VERSION_LABEL_PATTERN = r"^[^/\\]{1,96}$"


class PolicyDocumentIn(BaseModel):
    doc_id: str = Field(pattern=DOC_ID_PATTERN)
    title: str = Field(min_length=1, max_length=512)
    document_number: str = Field(default="", max_length=128)
    issuing_authority: str = Field(min_length=1, max_length=256)
    jurisdiction: str = Field(min_length=1, max_length=128)
    policy_level: str = Field(default="", max_length=64)
    source_type: Literal["external", "internal"] = "external"
    permission_level: int = Field(default=1, ge=1, le=2)
    meta: dict = Field(default_factory=dict)


class PolicyVersionIn(BaseModel):
    version_label: str = Field(pattern=VERSION_LABEL_PATTERN)
    document_number: str = Field(default="", max_length=128)
    published_on: date | None = None
    effective_from: date
    effective_to: date | None = None
    source_url: str = Field(default="", max_length=1024)
    source_retrieved_at: datetime | None = None
    source_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    raw_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> PolicyVersionIn:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to 必须晚于 effective_from")
        return self


class PolicyClauseIn(BaseModel):
    clause_no: str = Field(min_length=1, max_length=64)
    path: str = Field(default="", max_length=512)
    content: str = Field(min_length=1)
    conditions: dict = Field(default_factory=dict)
    exceptions: dict = Field(default_factory=dict)
    seq: int = Field(default=0, ge=0)
    meta: dict = Field(default_factory=dict)


class PolicyRelationIn(BaseModel):
    to_version_id: str = Field(min_length=1, max_length=192)
    relation_type: Literal[
        "amended_by", "repealed_by", "replaced_by", "implements", "references"
    ]
    basis: str = Field(default="", max_length=2000)


class PolicyImportIn(BaseModel):
    document: PolicyDocumentIn
    version: PolicyVersionIn
    clauses: list[PolicyClauseIn] = Field(min_length=1, max_length=1000)
    relations: list[PolicyRelationIn] = Field(default_factory=list, max_length=50)


class PolicyReviewIn(BaseModel):
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class PolicyClauseUpdateIn(BaseModel):
    content: str = Field(min_length=1)
    path: str = Field(default="", max_length=512)
    conditions: dict = Field(default_factory=dict)
    exceptions: dict = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class ApplicableQueryIn(BaseModel):
    jurisdiction: str = Field(min_length=1, max_length=128)
    as_of: date = Field(default_factory=date.today)
    document_number: str = Field(default="", max_length=128)
    subject: str = Field(default="", max_length=256)


class PolicySearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    jurisdiction: str = Field(default="全国", min_length=1, max_length=128)
    subject: str = Field(default="", max_length=256)
    as_of: date = Field(default_factory=date.today)
    top_k: int = Field(default=8, ge=1, le=30)


class PolicyInterpretIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    jurisdiction: str | None = Field(default=None, max_length=128)
    subject: str = Field(default="", max_length=256)
    as_of: date | None = None
    top_k: int = Field(default=6, ge=1, le=20)


class InterpretSessionCreateIn(PolicyInterpretIn):
    pass


class InterpretSessionTurnIn(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    jurisdiction: str | None = Field(default=None, max_length=128)
    subject: str | None = Field(default=None, max_length=256)
    as_of: date | None = None

    @model_validator(mode="after")
    def require_update(self) -> InterpretSessionTurnIn:
        if (
            self.question is None
            and self.jurisdiction is None
            and self.subject is None
            and self.as_of is None
        ):
            raise ValueError("至少提供一个会话更新字段")
        return self


class PolicySupportIn(BaseModel):
    claim: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(min_length=1, max_length=20)
    use_semantic: bool = False


class DraftFactIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ref: str = Field(default="", max_length=512)


class DraftSectionIn(BaseModel):
    heading: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=8000)


class DocumentDraftIn(BaseModel):
    doc_type: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=512)
    recipient: str = Field(default="", max_length=256)
    issuing_authority: str = Field(min_length=1, max_length=256)
    document_date: date
    purpose: str = Field(min_length=1, max_length=4000)
    facts: list[DraftFactIn] = Field(default_factory=list, max_length=100)
    sections: list[DraftSectionIn] = Field(default_factory=list, max_length=50)
    policy_citations: list[str] = Field(default_factory=list, max_length=20)


class DocumentDraftUpdateIn(DocumentDraftIn):
    expected_version: int = Field(ge=1)


class DocumentDraftReviewIn(BaseModel):
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


class WritingMaterialIn(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    source_ref: str = Field(default="", max_length=512)


class WritingOutlineIn(BaseModel):
    heading: str = Field(min_length=1, max_length=200)


class WritingDraftIn(BaseModel):
    mode: Literal["content_writing", "summarization", "consultation_draft"]
    request: str = Field(min_length=1, max_length=4000)
    audience: str = Field(default="", max_length=256)
    jurisdiction: str = Field(default="", max_length=128)
    subject: str = Field(default="", max_length=256)
    as_of: date | None = None
    materials: list[WritingMaterialIn] = Field(min_length=1, max_length=100)
    outline: list[WritingOutlineIn] = Field(default_factory=list, max_length=30)
    policy_citations: list[str] = Field(default_factory=list, max_length=20)


class WritingDraftUpdateIn(WritingDraftIn):
    expected_version: int = Field(ge=1)
