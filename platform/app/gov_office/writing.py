"""政务文稿、提炼总结和咨询拟稿：抽取式草稿、来源映射、版本与审核。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import jieba
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.errors import BusinessError
from app.gov_office.schemas import (
    DocumentDraftReviewIn,
    WritingDraftIn,
    WritingDraftUpdateIn,
)
from app.gov_office.security import append_gov_audit, ensure_safe_text
from app.gov_office.services import GovPolicyService
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from domains.gov_office.models import GovWritingDraft

REVIEW_ROLES = {Roles.KB_ADMIN, Roles.PLATFORM_ADMIN, Roles.SEC_AUDITOR}
TASK_MARKERS = ("负责", "完成", "落实", "推进", "时限", "前完成", "责任人")
HIGH_RISK_MARKERS = ("审批", "许可", "处罚", "资格", "行政决定", "强制", "补贴")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload_hash(payload: WritingDraftIn) -> str:
    raw = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(raw)


def _view(row: GovWritingDraft) -> dict[str, Any]:
    return {
        "draft_id": row.draft_id,
        "owner_id": row.owner_id,
        "mode": row.mode,
        "request": row.request,
        "audience": row.audience,
        "jurisdiction": row.jurisdiction,
        "subject": row.subject,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "materials": row.materials or [],
        "outline": row.outline or [],
        "policy_citations": row.policy_citations or [],
        "result": row.result or {},
        "checks": row.checks or [],
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


def _sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[。！？；])|\n+", text)
        if part.strip()
    ]


class GovWritingService:
    def __init__(self, db: Session):
        self.db = db

    def _guard_inputs(self, payload: WritingDraftIn) -> None:
        ensure_safe_text(
            payload.request,
            payload.audience,
            payload.jurisdiction,
            payload.subject,
            *(item.text for item in payload.materials),
            *(item.heading for item in payload.outline),
        )

    async def create(
        self, payload: WritingDraftIn, actor: Actor, idempotency_key: str
    ) -> dict[str, Any]:
        self._guard_inputs(payload)
        request_hash = _payload_hash(payload)
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT", "幂等键对应不同文稿内容", 409
                )
            return {"draft": _view(existing), "duplicated": True}
        result, checks, status = await self._generate(payload, actor)
        row = GovWritingDraft(
            draft_id=f"WR-{uuid.uuid4().hex[:20]}",
            owner_id=actor.user_id,
            mode=payload.mode,
            request=payload.request,
            audience=payload.audience,
            jurisdiction=payload.jurisdiction,
            subject=payload.subject,
            as_of=payload.as_of,
            materials=[item.model_dump() for item in payload.materials],
            outline=[item.model_dump() for item in payload.outline],
            policy_citations=payload.policy_citations,
            result=result,
            checks=checks,
            status=status,
            version=1,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            content_hash=request_hash,
        )
        self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            duplicate = self._by_idempotency(idempotency_key)
            if duplicate is not None and duplicate.request_hash == request_hash:
                return {"draft": _view(duplicate), "duplicated": True}
            raise BusinessError("WRITING_DRAFT_CONFLICT", "文稿草稿并发冲突", 409) from exc
        return {"draft": _view(row), "duplicated": False}

    async def update(
        self,
        draft_id: str,
        payload: WritingDraftUpdateIn,
        actor: Actor,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._guard_inputs(payload)
        row = self._owned(draft_id, actor)
        if row.status == "approved":
            raise BusinessError("WRITING_DRAFT_IMMUTABLE", "已审核文稿不可原地修改", 409)
        request_hash = _payload_hash(payload)
        if request_hash == row.content_hash:
            return {"draft": _view(row), "duplicated": True}
        if payload.expected_version != row.version:
            raise BusinessError(
                "PRECONDITION_FAILED", f"文稿已被修改，当前 version={row.version}", 412
            )
        result, checks, status = await self._generate(payload, actor)
        row.mode = payload.mode
        row.request = payload.request
        row.audience = payload.audience
        row.jurisdiction = payload.jurisdiction
        row.subject = payload.subject
        row.as_of = payload.as_of
        row.materials = [item.model_dump() for item in payload.materials]
        row.outline = [item.model_dump() for item in payload.outline]
        row.policy_citations = payload.policy_citations
        row.result = result
        row.checks = checks
        row.status = status
        row.version += 1
        row.content_hash = request_hash
        row.reviewed_by = None
        row.reviewed_at = None
        row.review_note = ""
        self.db.commit()
        return {"draft": _view(row), "duplicated": False}

    def review(
        self, draft_id: str, payload: DocumentDraftReviewIn, actor: Actor
    ) -> dict[str, Any]:
        row = self._get(draft_id)
        if row.owner_id == actor.user_id:
            raise BusinessError(
                "DOCUMENT_REVIEW_SEPARATION", "起草人不能审核自己的文稿", 409
            )
        target = "approved" if payload.action == "approve" else "rejected"
        if row.status == target:
            return _view(row)
        if row.status != "pending_review":
            raise BusinessError(
                "DOCUMENT_REVIEW_STATE_INVALID", f"文稿状态 {row.status} 不允许审核", 409
            )
        row.status = target
        row.reviewed_by = actor.user_id
        row.reviewed_at = datetime.now(UTC)
        row.review_note = payload.note
        append_gov_audit(
            self.db,
            actor,
            action="gov.writing.review",
            resource=row.draft_id,
            detail={
                "decision": target,
                "content_hash": row.content_hash,
                "version": row.version,
                "mode": row.mode,
                "note": payload.note,
            },
        )
        self.db.commit()
        return _view(row)

    def list_drafts(
        self,
        actor: Actor,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> dict[str, Any]:
        query = self.db.query(GovWritingDraft)
        if actor.role_id not in REVIEW_ROLES:
            query = query.filter(GovWritingDraft.owner_id == actor.user_id)
        if status:
            query = query.filter(GovWritingDraft.status == status)
        total = query.count()
        rows = (
            query.order_by(GovWritingDraft.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "items": [_view(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get(self, draft_id: str, actor: Actor) -> dict[str, Any]:
        row = self._get(draft_id)
        if row.owner_id != actor.user_id and actor.role_id not in REVIEW_ROLES:
            raise BusinessError("WRITING_DRAFT_NOT_FOUND", "文稿草稿不存在", 404)
        return _view(row)

    async def _generate(
        self, payload: WritingDraftIn, actor: Actor
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        checks: list[dict[str, Any]] = []
        valid_citations = self._valid_citations(
            payload.policy_citations, checks, actor.permission_max
        )
        if payload.mode == "summarization":
            result = self._summarize(payload)
        elif payload.mode == "content_writing":
            result = self._write_content(payload, checks)
        else:
            result = await self._draft_consultation(payload, checks, actor)
        result["policy_citations"] = [
            {
                "citation_id": citation_id,
                **self._citation_detail(citation_id, valid_citations),
            }
            for citation_id in payload.policy_citations
        ]
        if not payload.policy_citations:
            checks.append(
                {
                    "code": "NO_POLICY_CITATION",
                    "severity": "info",
                    "message": "未提供政策依据；如任务需要，请补充",
                    "location": "policy_citations",
                }
            )
        has_error = any(item["severity"] == "error" for item in checks)
        return result, checks, "needs_revision" if has_error else "pending_review"

    def _summarize(self, payload: WritingDraftIn) -> dict[str, Any]:
        units: list[str] = []
        key_points: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        for material_index, material in enumerate(payload.materials, start=1):
            parts = _sentences(material.text) or [material.text]
            for unit_index, sentence in enumerate(parts, start=1):
                unit_id = f"U-{material_index}-{unit_index}"
                units.append(unit_id)
                point = {
                    "point_id": f"KP-{len(key_points) + 1}",
                    "unit_id": unit_id,
                    "text": sentence,
                    "source_ref": material.source_ref,
                }
                key_points.append(point)
                if any(marker in sentence for marker in TASK_MARKERS):
                    tasks.append(
                        {
                            "task_id": f"T-{len(tasks) + 1}",
                            "text": sentence,
                            "source_ref": material.source_ref,
                            "unit_id": unit_id,
                        }
                    )
        summary = "；".join(item["text"] for item in key_points)[:800]
        return {
            "summary": summary,
            "key_points": key_points[:30],
            "tasks": tasks[:30],
            "sections": [
                {
                    "heading": "材料摘要",
                    "content": summary,
                    "source_refs": [
                        item.source_ref
                        for item in payload.materials
                        if item.source_ref
                    ],
                }
            ],
            "coverage": {
                "source_units": len(units),
                "represented_units": len({item["unit_id"] for item in key_points}),
                "ratio": 1.0 if units else 0.0,
            },
            "generation_mode": "extractive",
        }

    def _write_content(
        self, payload: WritingDraftIn, checks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not payload.outline:
            checks.append(
                {
                    "code": "OUTLINE_REQUIRED",
                    "severity": "error",
                    "message": "文稿撰写需先确认提纲",
                    "location": "outline",
                }
            )
            suggestions = [
                {"heading": "背景与依据"},
                {"heading": "主要工作或情况"},
                {"heading": "下一步安排"},
            ]
            return {
                "summary": "尚未确认提纲，未生成正文。",
                "sections": [],
                "outline_suggestions": suggestions,
                "generation_mode": "extractive",
            }
        sections = []
        used: set[int] = set()
        for heading in payload.outline:
            terms = {
                token.strip()
                for token in jieba.lcut_for_search(heading.heading)
                if len(token.strip()) >= 2
            }
            best_index = None
            best_score = 0
            for index, material in enumerate(payload.materials):
                score = sum(1 for term in terms if term in material.text)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index is None:
                sections.append(
                    {
                        "heading": heading.heading,
                        "content": "",
                        "source_refs": [],
                        "warning": "未找到与提纲匹配的素材，请补充或人工撰写",
                    }
                )
                checks.append(
                    {
                        "code": "OUTLINE_WITHOUT_SOURCE",
                        "severity": "warning",
                        "message": f"提纲「{heading.heading}」没有匹配素材",
                        "location": "outline",
                    }
                )
                continue
            material = payload.materials[best_index]
            used.add(best_index)
            sections.append(
                {
                    "heading": heading.heading,
                    "content": material.text[:1200],
                    "source_refs": [material.source_ref] if material.source_ref else [],
                }
            )
        return {
            "summary": "；".join(
                f"{section['heading']}：{section['content'][:80]}"
                for section in sections
                if section["content"]
            )[:800],
            "sections": sections,
            "coverage": {
                "source_materials": len(payload.materials),
                "used_materials": len(used),
                "ratio": round(len(used) / len(payload.materials), 4),
            },
            "generation_mode": "extractive",
        }

    async def _draft_consultation(
        self,
        payload: WritingDraftIn,
        checks: list[dict[str, Any]],
        actor: Actor,
    ) -> dict[str, Any]:
        if not payload.jurisdiction or payload.as_of is None:
            checks.append(
                {
                    "code": "CONSULTATION_CONTEXT_REQUIRED",
                    "severity": "error",
                    "message": "咨询拟稿需提供政策地区和事项时间",
                    "location": "jurisdiction/as_of",
                }
            )
            return {
                "public_reply": "",
                "internal_basis": [],
                "generation_mode": "extractive",
            }
        from app.gov_office.schemas import PolicyInterpretIn

        interpretation = await GovPolicyService(self.db).interpret(
            PolicyInterpretIn(
                question=payload.request,
                jurisdiction=payload.jurisdiction,
                subject=payload.subject,
                as_of=payload.as_of,
                top_k=6,
            ),
            actor.permission_max,
        )
        if interpretation["outcome"] != "answer":
            checks.append(
                {
                    "code": "CONSULTATION_INSUFFICIENT",
                    "severity": "error",
                    "message": "政策依据不足，不能形成咨询答复",
                    "location": "request",
                }
            )
            return {
                "public_reply": "",
                "internal_basis": [],
                "interpretation": interpretation,
                "generation_mode": "extractive",
            }
        public_reply = interpretation["summary"]
        if interpretation["claims"]:
            public_reply += "\n" + "\n".join(
                claim["text"] for claim in interpretation["claims"][:4]
            )
        categories: dict[str, list[dict[str, Any]]] = {
            "conditions": [],
            "materials": [],
            "channels": [],
            "time_limits": [],
        }
        classifiers = {
            "conditions": ("条件", "应当", "需要", "符合"),
            "materials": ("材料", "申请书", "证明"),
            "channels": ("渠道", "受理", "申请", "平台", "窗口"),
            "time_limits": ("工作日", "期限", "时限", "日内"),
        }
        for claim in interpretation["claims"]:
            matched_category = next(
                (
                    category
                    for category, markers in classifiers.items()
                    if any(marker in claim["text"] for marker in markers)
                ),
                None,
            )
            if matched_category:
                categories[matched_category].append(
                    {
                        "text": claim["text"],
                        "citation_ids": claim["citation_ids"],
                    }
                )
        high_risk = [
            marker for marker in HIGH_RISK_MARKERS if marker in payload.request
        ]
        if high_risk:
            checks.append(
                {
                    "code": "CONSULTATION_HIGH_RISK",
                    "severity": "warning",
                    "message": "问题涉及审批、资格或裁量事项，必须转人工确认",
                    "location": "request",
                }
            )
        return {
            "public_reply": public_reply,
            "internal_basis": interpretation["citations"],
            "materials": [item.model_dump() for item in payload.materials],
            "conditions": categories["conditions"],
            "required_materials": categories["materials"],
            "channels": categories["channels"],
            "time_limits": categories["time_limits"],
            "interpretation": interpretation,
            "generation_mode": "extractive",
            "high_risk_markers": high_risk,
            "manual_review_required": True,
            "publish_status": "draft_only",
        }

    def _valid_citations(
        self,
        citation_ids: list[str],
        checks: list[dict[str, Any]],
        permission_max: int,
    ) -> dict[str, Any]:
        if not citation_ids:
            return {}
        found = GovPolicyService(self.db).resolve_citations(
            citation_ids, permission_max
        )
        for citation_id in citation_ids:
            if citation_id not in found:
                checks.append(
                    {
                        "code": "POLICY_CITATION_INVALID",
                        "severity": "error",
                        "message": f"政策引用无效或未发布：{citation_id}",
                        "location": "policy_citations",
                    }
                )
        return found

    def _citation_detail(self, citation_id: str, found: dict[str, Any]) -> dict[str, Any]:
        row = found.get(citation_id)
        if row is None:
            return {"status": "invalid"}
        clause, version, doc = row
        return {
            "status": "valid",
            "doc_id": doc.doc_id,
            "title": doc.title,
            "version_id": version.version_id,
            "clause_no": clause.clause_no,
        }

    def _get(self, draft_id: str) -> GovWritingDraft:
        row = self.db.get(GovWritingDraft, draft_id)
        if row is None:
            raise BusinessError("WRITING_DRAFT_NOT_FOUND", "文稿草稿不存在", 404)
        return row

    def _owned(self, draft_id: str, actor: Actor) -> GovWritingDraft:
        row = self._get(draft_id)
        if row.owner_id != actor.user_id:
            raise BusinessError("WRITING_DRAFT_NOT_FOUND", "文稿草稿不存在", 404)
        return row

    def _by_idempotency(self, key: str) -> GovWritingDraft | None:
        return (
            self.db.query(GovWritingDraft)
            .filter(GovWritingDraft.idempotency_key == key)
            .first()
        )
