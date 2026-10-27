"""政务公文结构化草稿：模板装配、事实与数字核对、版本和人工审核。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.errors import BusinessError
from app.gov_office.schemas import (
    DocumentDraftIn,
    DocumentDraftReviewIn,
    DocumentDraftUpdateIn,
)
from app.gov_office.security import append_gov_audit, ensure_safe_text
from app.gov_office.services import GovPolicyService
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from domains.gov_office.models import GovDocumentDraft

RULES_PATH = (
    Path(__file__).resolve().parents[2]
    / "domains"
    / "gov_office"
    / "templates"
    / "gov_document_rules.yaml"
)
REQUIRED_FIELD_LABELS = {
    "title": "标题",
    "recipient": "主送对象",
    "issuing_authority": "发文机关",
    "document_date": "成文日期",
}
REVIEW_ROLES = {Roles.KB_ADMIN, Roles.PLATFORM_ADMIN, Roles.SEC_AUDITOR}
NUMBER_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?%?)")


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload_hash(payload: DocumentDraftIn) -> str:
    raw = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(raw)


def _content_hash(
    doc_type: str,
    title: str,
    recipient: str,
    issuing_authority: str,
    document_date: str,
    purpose: str,
    facts: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    citations: list[str],
) -> str:
    raw = json.dumps(
        {
            "doc_type": doc_type,
            "title": title,
            "recipient": recipient,
            "issuing_authority": issuing_authority,
            "document_date": document_date,
            "purpose": purpose,
            "facts": facts,
            "sections": sections,
            "citations": citations,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(raw)


def _draft_view(row: GovDocumentDraft) -> dict[str, Any]:
    return {
        "draft_id": row.draft_id,
        "owner_id": row.owner_id,
        "doc_type": row.doc_type,
        "title": row.title,
        "recipient": row.recipient,
        "issuing_authority": row.issuing_authority,
        "document_date": row.document_date.isoformat(),
        "purpose": row.purpose,
        "sections": row.sections or [],
        "facts": row.facts or [],
        "policy_citations": row.policy_citations or [],
        "checks": row.checks or [],
        "template": {
            "template_id": row.template_id,
            "version": row.template_version,
        },
        "status": row.status,
        "version": row.version,
        "content_hash": row.content_hash,
        "review": {
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "note": row.review_note,
        },
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class GovDocumentService:
    def __init__(self, db: Session):
        self.db = db
        self.rules = load_rules()

    def _guard_inputs(self, payload: DocumentDraftIn) -> None:
        texts = [
            payload.title,
            payload.recipient,
            payload.issuing_authority,
            payload.purpose,
            *(item.text for item in payload.facts),
            *(item.heading for item in payload.sections),
            *(item.content for item in payload.sections),
        ]
        ensure_safe_text(*texts)

    def create(
        self, payload: DocumentDraftIn, actor: Actor, idempotency_key: str
    ) -> dict[str, Any]:
        self._guard_inputs(payload)
        existing = (
            self.db.query(GovDocumentDraft)
            .filter(GovDocumentDraft.idempotency_key == idempotency_key)
            .first()
        )
        request_hash = _payload_hash(payload)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT", "幂等键对应不同草稿内容", 409
                )
            return {"draft": _draft_view(existing), "duplicated": True}
        template = self._template(payload.doc_type)
        sections = self._assemble_sections(payload)
        checks = self._checks(payload, template, sections, actor.permission_max)
        status = (
            "needs_revision"
            if any(item["severity"] == "error" for item in checks)
            else "pending_review"
        )
        row = GovDocumentDraft(
            draft_id=f"DOC-{uuid.uuid4().hex[:20]}",
            owner_id=actor.user_id,
            doc_type=payload.doc_type,
            title=payload.title,
            recipient=payload.recipient,
            issuing_authority=payload.issuing_authority,
            document_date=payload.document_date,
            purpose=payload.purpose,
            sections=sections,
            facts=[item.model_dump() for item in payload.facts],
            policy_citations=payload.policy_citations,
            checks=checks,
            template_id=f"gov-doc-{payload.doc_type}",
            template_version=str(self.rules.get("ruleset_version", "unknown")),
            status=status,
            version=1,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            content_hash=self._content_hash(payload, sections),
        )

        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            existing = (
                self.db.query(GovDocumentDraft)
                .filter(GovDocumentDraft.idempotency_key == idempotency_key)
                .first()
            )
            if existing is not None and existing.request_hash == request_hash:
                return {"draft": _draft_view(existing), "duplicated": True}
            raise BusinessError(
                "DOCUMENT_DRAFT_CONFLICT", "公文草稿并发写入冲突", 409
            ) from exc
        return {"draft": _draft_view(row), "duplicated": False}

    def update(
        self,
        draft_id: str,
        payload: DocumentDraftUpdateIn,
        actor: Actor,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._guard_inputs(payload)
        row = self._owned_draft(draft_id, actor)
        if row.status == "approved":
            raise BusinessError(
                "DOCUMENT_DRAFT_IMMUTABLE", "已审核公文不可原地修改", 409
            )
        sections = self._assemble_sections(payload)
        content_hash = self._content_hash(payload, sections)
        if content_hash == row.content_hash:
            return {"draft": _draft_view(row), "duplicated": True}
        if payload.expected_version != row.version:
            raise BusinessError(
                "PRECONDITION_FAILED",
                f"草稿已被修改，当前 version={row.version}",
                412,
            )
        template = self._template(payload.doc_type)
        checks = self._checks(payload, template, sections, actor.permission_max)
        row.doc_type = payload.doc_type
        row.title = payload.title
        row.recipient = payload.recipient
        row.issuing_authority = payload.issuing_authority
        row.document_date = payload.document_date
        row.purpose = payload.purpose
        row.sections = sections
        row.facts = [item.model_dump() for item in payload.facts]
        row.policy_citations = payload.policy_citations
        row.checks = checks
        row.template_id = f"gov-doc-{payload.doc_type}"
        row.template_version = str(self.rules.get("ruleset_version", "unknown"))
        row.status = (
            "needs_revision"
            if any(item["severity"] == "error" for item in checks)
            else "pending_review"
        )
        row.version += 1
        row.content_hash = content_hash
        row.reviewed_by = None
        row.reviewed_at = None
        row.review_note = ""
        self.db.commit()
        return {"draft": _draft_view(row), "duplicated": False}

    def review(
        self,
        draft_id: str,
        payload: DocumentDraftReviewIn,
        actor: Actor,
    ) -> dict[str, Any]:
        row = self._get(draft_id)
        if row.owner_id == actor.user_id:
            raise BusinessError(
                "DOCUMENT_REVIEW_SEPARATION", "起草人不能审核自己的公文草稿", 409
            )
        target = "approved" if payload.action == "approve" else "rejected"
        if row.status == target:
            return _draft_view(row)
        if row.status != "pending_review":
            raise BusinessError(
                "DOCUMENT_REVIEW_STATE_INVALID",
                f"草稿状态 {row.status} 不允许审核",
                409,
            )
        row.status = target
        row.reviewed_by = actor.user_id
        row.reviewed_at = datetime.now(UTC)
        row.review_note = payload.note
        append_gov_audit(
            self.db,
            actor,
            action="gov.document.review",
            resource=row.draft_id,
            detail={
                "decision": target,
                "content_hash": row.content_hash,
                "version": row.version,
                "policy_citations": row.policy_citations,
                "note": payload.note,
            },
        )
        self.db.commit()
        return _draft_view(row)

    def list_drafts(
        self,
        actor: Actor,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> dict[str, Any]:
        query = self.db.query(GovDocumentDraft)
        if actor.role_id not in REVIEW_ROLES:
            query = query.filter(GovDocumentDraft.owner_id == actor.user_id)
        if status:
            query = query.filter(GovDocumentDraft.status == status)
        total = query.count()
        rows = (
            query.order_by(GovDocumentDraft.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "items": [_draft_view(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get(self, draft_id: str, actor: Actor) -> dict[str, Any]:
        row = self._get(draft_id)
        if row.owner_id != actor.user_id and actor.role_id not in REVIEW_ROLES:
            raise BusinessError("DOCUMENT_DRAFT_NOT_FOUND", "公文草稿不存在", 404)
        return _draft_view(row)

    def _assemble_sections(self, payload: DocumentDraftIn) -> list[dict[str, str]]:
        if payload.sections:
            return [item.model_dump() for item in payload.sections]
        if payload.facts:
            facts_text = "\n".join(item.text for item in payload.facts)
        else:
            facts_text = "当前未提供经核实的事实材料。"
        return [
            {"heading": "一、基本情况", "content": facts_text},
            {"heading": "二、主要事项", "content": payload.purpose},
        ]

    def _checks(
        self,
        payload: DocumentDraftIn,
        template: dict[str, Any],
        sections: list[dict[str, str]],
        permission_max: int,
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        machine = template.get("machine_checks") or {}
        for field in machine.get("required_fields") or []:
            value = getattr(payload, field, None)
            if value in (None, ""):
                checks.append(
                    {
                        "code": "REQUIRED_FIELD_MISSING",
                        "severity": "error",
                        "message": f"缺少必填要素：{REQUIRED_FIELD_LABELS.get(field, field)}",
                        "location": field,
                    }
                )
        body_text = "\n".join(
            [payload.purpose, *(section["content"] for section in sections)]
        )
        minimum = int(machine.get("min_body_chars", 0))
        if len("".join(body_text.split())) < minimum:
            checks.append(
                {
                    "code": "BODY_TOO_SHORT",
                    "severity": "error",
                    "message": f"正文少于模板要求的最小长度 {minimum}",
                    "location": "sections",
                }
            )
        fact_text = " ".join(item.text for item in payload.facts)
        for number in dict.fromkeys(NUMBER_RE.findall(body_text)):
            if number not in fact_text:
                checks.append(
                    {
                        "code": "UNSOURCED_NUMBER",
                        "severity": "error",
                        "message": f"数字或日期缺少事实来源：{number}",
                        "location": "sections",
                    }
                )
        if payload.doc_type == "report" and any(
            marker in body_text for marker in ("请示", "请批准", "请批复")
        ):
            checks.append(
                {
                    "code": "REPORT_CONTAINS_REQUEST",
                    "severity": "error",
                    "message": "报告中不得夹带请示事项",
                    "location": "sections",
                }
            )
        if payload.doc_type == "minutes" and not payload.facts:
            checks.append(
                {
                    "code": "MINUTES_WITHOUT_RECORD",
                    "severity": "error",
                    "message": "纪要缺少会议记录事实材料",
                    "location": "facts",
                }
            )
        if payload.policy_citations:
            found = GovPolicyService(self.db).resolve_citations(
                payload.policy_citations, permission_max
            )
            for citation_id in payload.policy_citations:
                if citation_id not in found:
                    checks.append(
                        {
                            "code": "POLICY_CITATION_INVALID",
                            "severity": "error",
                            "message": f"政策引用不存在、无权或未发布：{citation_id}",
                            "location": "policy_citations",
                        }
                    )
        if not payload.policy_citations:
            checks.append(
                {
                    "code": "NO_POLICY_CITATION",
                    "severity": "info",
                    "message": "未提供政策依据；如公文要求引用政策，请补充",
                    "location": "policy_citations",
                }
            )
        return checks

    def _content_hash(
        self, payload: DocumentDraftIn, sections: list[dict[str, str]]
    ) -> str:
        return _content_hash(
            payload.doc_type,
            payload.title,
            payload.recipient,
            payload.issuing_authority,
            payload.document_date.isoformat(),
            payload.purpose,
            [item.model_dump() for item in payload.facts],
            sections,
            payload.policy_citations,
        )

    def _template(self, doc_type: str) -> dict[str, Any]:
        template = (self.rules.get("document_types") or {}).get(doc_type)
        if template is None:
            raise BusinessError(
                "VALIDATION_ERROR", f"不支持的公文文种：{doc_type}", 400
            )
        return template

    def _get(self, draft_id: str) -> GovDocumentDraft:
        row = self.db.get(GovDocumentDraft, draft_id)
        if row is None:
            raise BusinessError("DOCUMENT_DRAFT_NOT_FOUND", "公文草稿不存在", 404)
        return row

    def _owned_draft(self, draft_id: str, actor: Actor) -> GovDocumentDraft:
        row = self._get(draft_id)
        if row.owner_id != actor.user_id:
            raise BusinessError("DOCUMENT_DRAFT_NOT_FOUND", "公文草稿不存在", 404)
        return row
