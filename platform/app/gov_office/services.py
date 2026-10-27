"""政策知识服务：导入幂等、人工审核、版本适用、检索和版本对照。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

import jieba
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.errors import BusinessError
from app.gov_office.schemas import (
    ApplicableQueryIn,
    PolicyClauseUpdateIn,
    PolicyImportIn,
    PolicyInterpretIn,
    PolicyReviewIn,
    PolicySearchIn,
    PolicySupportIn,
)
from app.gov_office.security import append_gov_audit, ensure_safe_text
from core.abstraction.dtos import Actor
from core.knowledge.ingest import embedding_model_name
from core.runtime import get_model_gateway
from domains.gov_office.models import (
    GovPolicyClause,
    GovPolicyDocument,
    GovPolicyImportOperation,
    GovPolicyRelation,
    GovPolicyVersion,
)

REVIEWED = "approved"
PUBLISHED = "published"
# 嵌入服务批量上限不一致：text-embedding-v3 单请求最多 10 条，
# qwen3.7-text-embedding 最多 25 条；统一按 10 条分批。
EMBED_BATCH_SIZE = 10
logger = logging.getLogger(__name__)
STOP_TOKENS = {
    "什么",
    "哪些",
    "如何",
    "怎么",
    "是否",
    "可以",
    "应当",
    "关于",
    "有关",
    "以及",
    "这个",
    "那个",
    "问题",
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(payload: PolicyImportIn) -> str:
    raw = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(raw)


def _version_id(doc_id: str, version_label: str) -> str:
    return f"{doc_id}:{version_label}"


def version_state(version: GovPolicyVersion, as_of: date) -> str:
    """版本在指定日期的状态；effective_to 为开区间上界。"""
    if version.review_status != REVIEWED:
        return "unreviewed"
    if version.published_on is not None and version.published_on > as_of:
        return "not_published"
    if version.effective_from > as_of:
        return "not_yet_effective"
    if version.effective_to is not None and as_of >= version.effective_to:
        return "expired"
    return "effective"


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def document_view(row: GovPolicyDocument) -> dict[str, Any]:
    return {
        "doc_id": row.doc_id,
        "title": row.title,
        "document_number": row.document_number,
        "issuing_authority": row.issuing_authority,
        "jurisdiction": row.jurisdiction,
        "policy_level": row.policy_level,
        "knowledge_status": row.knowledge_status,
        "source_type": row.source_type,
        "permission_level": row.permission_level,
        "meta": row.meta or {},
        "updated_at": _iso(row.updated_at),
    }


def version_view(row: GovPolicyVersion, as_of: date | None = None) -> dict[str, Any]:
    data = {
        "version_id": row.version_id,
        "doc_id": row.doc_id,
        "version_label": row.version_label,
        "document_number": row.document_number,
        "revision": row.revision,
        "review_status": row.review_status,
        "published_on": _iso(row.published_on),
        "effective_from": _iso(row.effective_from),
        "effective_to": _iso(row.effective_to),
        "source_url": row.source_url,
        "source_retrieved_at": _iso(row.source_retrieved_at),
        "source_hash": row.source_hash,
        "review_note": row.review_note,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": _iso(row.reviewed_at),
    }
    if as_of is not None:
        data["state_at"] = version_state(row, as_of)
    return data


def clause_view(clause: GovPolicyClause, version: GovPolicyVersion) -> dict[str, Any]:
    return {
        "clause_id": clause.clause_id,
        "version_id": clause.version_id,
        "doc_id": version.doc_id,
        "clause_no": clause.clause_no,
        "path": clause.path,
        "content": clause.content,
        "conditions": clause.conditions or {},
        "exceptions": clause.exceptions or {},
        "seq": clause.seq,
        "citation_id": f"{clause.clause_id}@{version.version_label}",
        "meta": clause.meta or {},
    }


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


class GovPolicyService:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- 导入与审核 ----------------
    def import_policy(
        self, payload: PolicyImportIn, actor: Actor, idempotency_key: str
    ) -> dict[str, Any]:
        request_hash = _request_hash(payload)
        operation = self.db.execute(
            select(GovPolicyImportOperation).where(
                GovPolicyImportOperation.idempotency_key == idempotency_key
            )
        ).scalars().first()
        if operation is not None:
            if operation.request_hash != request_hash:
                raise BusinessError("IDEMPOTENCY_CONFLICT", "幂等键对应不同导入内容", 409)
            return self._import_result(operation.doc_id, operation.version_id, duplicated=True)

        version_id = _version_id(payload.document.doc_id, payload.version.version_label)
        actual_hash = _sha256(payload.version.raw_text)
        if actual_hash.lower() != payload.version.source_hash.lower():
            raise BusinessError("POLICY_SOURCE_HASH_MISMATCH", "正文哈希与原文不一致", 400)

        doc = self.db.get(GovPolicyDocument, payload.document.doc_id)
        if doc is None:
            doc = GovPolicyDocument(
                doc_id=payload.document.doc_id,
                title=payload.document.title,
                document_number=payload.document.document_number,
                issuing_authority=payload.document.issuing_authority,
                jurisdiction=payload.document.jurisdiction,
                policy_level=payload.document.policy_level,
                source_type=payload.document.source_type,
                permission_level=payload.document.permission_level,
                meta=payload.document.meta,
                created_by=actor.user_id,
            )
            self.db.add(doc)
            self.db.flush()
        else:
            self._assert_same_document(doc, payload)

        existing = self._get_version(payload.document.doc_id, payload.version.version_label)
        if existing is not None:
            if existing.source_hash.lower() != actual_hash.lower():
                raise BusinessError("POLICY_VERSION_CONFLICT", "该版本已存在不同正文", 409)
            self._add_operation(idempotency_key, request_hash, payload, actor)
            self.db.commit()
            return self._import_result(payload.document.doc_id, existing.version_id, duplicated=True)

        version = GovPolicyVersion(
            version_id=version_id,
            doc_id=payload.document.doc_id,
            version_label=payload.version.version_label,
            document_number=payload.version.document_number,
            published_on=payload.version.published_on,
            effective_from=payload.version.effective_from,
            effective_to=payload.version.effective_to,
            source_url=payload.version.source_url,
            source_retrieved_at=payload.version.source_retrieved_at
            or datetime.now(UTC),
            source_hash=actual_hash,
            raw_text=payload.version.raw_text,
            created_by=actor.user_id,
        )
        self.db.add(version)
        self.db.flush()
        self.db.add_all(self._build_clauses(version_id, payload))
        self.db.add_all(self._build_relations(version_id, payload, actor))
        self._add_operation(idempotency_key, request_hash, payload, actor)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            duplicate = self.db.execute(
                select(GovPolicyImportOperation).where(
                    GovPolicyImportOperation.idempotency_key == idempotency_key
                )
            ).scalars().first()
            if duplicate is not None and duplicate.request_hash == request_hash:
                return self._import_result(duplicate.doc_id, duplicate.version_id, duplicated=True)
            raise BusinessError("POLICY_VERSION_CONFLICT", "政策版本并发写入冲突", 409) from exc
        return self._import_result(payload.document.doc_id, version_id, duplicated=False)

    def review_version(
        self,
        doc_id: str,
        version_label: str,
        payload: PolicyReviewIn,
        actor: Actor,
    ) -> dict[str, Any]:
        version = self._require_version(doc_id, version_label)
        target = "approved" if payload.action == "approve" else "rejected"
        if version.review_status == target:
            return version_view(version)
        if version.review_status != "draft":
            raise BusinessError(
                "POLICY_REVIEW_STATE_INVALID",
                f"版本状态 {version.review_status} 不允许转为 {target}",
                409,
            )
        version.review_status = target
        version.reviewed_by = actor.user_id
        version.reviewed_at = datetime.now(UTC)
        version.review_note = payload.note
        append_gov_audit(
            self.db,
            actor,
            action="gov.policy.version.review",
            resource=version.version_id,
            detail={
                "decision": target,
                "source_hash": version.source_hash,
                "revision": version.revision,
                "note": payload.note,
            },
        )
        self.db.commit()
        return version_view(version)

    def update_clause(
        self,
        doc_id: str,
        version_label: str,
        clause_no: str,
        payload: PolicyClauseUpdateIn,
        actor: Actor,
    ) -> dict[str, Any]:
        version = self._require_version(doc_id, version_label)
        if version.review_status != "draft":
            raise BusinessError(
                "POLICY_VERSION_IMMUTABLE",
                "已审核政策版本不可修改，请导入新版本",
                409,
            )
        clause = self.db.execute(
            select(GovPolicyClause).where(
                GovPolicyClause.version_id == version.version_id,
                GovPolicyClause.clause_no == clause_no,
            )
        ).scalars().first()
        same_content = clause is not None and (
            clause.content,
            clause.path,
            clause.conditions,
            clause.exceptions,
        ) == (
            payload.content,
            payload.path,
            payload.conditions,
            payload.exceptions,
        )
        if same_content:
            return {
                "clause": clause_view(clause, version),
                "revision": version.revision,
                "duplicated": True,
            }
        if payload.expected_revision != version.revision:
            raise BusinessError(
                "PRECONDITION_FAILED",
                f"版本已被修改，当前 revision={version.revision}",
                412,
            )
        if clause is None:
            clause = GovPolicyClause(
                clause_id="CL-" + _sha256(f"{version.version_id}|{clause_no}")[:32],
                version_id=version.version_id,
                clause_no=clause_no,
                seq=self._next_clause_seq(version.version_id),
            )
            self.db.add(clause)
        clause.content = payload.content
        clause.path = payload.path
        clause.conditions = payload.conditions
        clause.exceptions = payload.exceptions
        version.revision += 1
        append_gov_audit(
            self.db,
            actor,
            action="gov.policy.clause.update",
            resource=clause.clause_id,
            detail={
                "version_id": version.version_id,
                "clause_no": clause_no,
                "revision": version.revision,
            },
        )
        self.db.commit()
        return {
            "clause": clause_view(clause, version),
            "revision": version.revision,
            "duplicated": False,
        }

    def publish_document(self, doc_id: str, actor: Actor) -> dict[str, Any]:
        doc = self._require_document(doc_id)
        if doc.knowledge_status == PUBLISHED:
            return document_view(doc)
        approved = self.db.scalar(
            select(func.count())
            .select_from(GovPolicyVersion)
            .where(
                GovPolicyVersion.doc_id == doc_id,
                GovPolicyVersion.review_status == REVIEWED,
            )
        )
        if not approved:
            raise BusinessError("POLICY_PUBLISH_NO_APPROVED", "没有已审核版本可发布", 409)
        doc.knowledge_status = PUBLISHED
        doc.updated_at = datetime.now(UTC)
        append_gov_audit(
            self.db,
            actor,
            action="gov.policy.publish",
            resource=doc.doc_id,
            detail={"approved_versions": int(approved)},
        )
        self.db.commit()
        return document_view(doc)

    # ---------------- 查询 ----------------
    def review_relation(
        self, relation_id: str, payload: PolicyReviewIn, actor: Actor
    ) -> dict[str, Any]:
        relation = self.db.get(GovPolicyRelation, relation_id)
        if relation is None:
            raise BusinessError("POLICY_RELATION_NOT_FOUND", "政策版本关系不存在", 404)
        target = "approved" if payload.action == "approve" else "rejected"
        if relation.review_status == target:
            return self._relation_view(relation)
        if relation.review_status != "draft":
            raise BusinessError(
                "POLICY_REVIEW_STATE_INVALID",
                f"关系状态 {relation.review_status} 不允许转为 {target}",
                409,
            )
        if target == "approved" and self.db.get(
            GovPolicyVersion, relation.to_version_id
        ) is None:
            raise BusinessError(
                "POLICY_RELATION_TARGET_MISSING", "关系目标版本尚未导入", 409
            )
        relation.review_status = target
        relation.reviewed_by = actor.user_id
        relation.reviewed_at = datetime.now(UTC)
        append_gov_audit(
            self.db,
            actor,
            action="gov.policy.relation.review",
            resource=relation.relation_id,
            detail={
                "decision": target,
                "from_version_id": relation.from_version_id,
                "to_version_id": relation.to_version_id,
                "relation_type": relation.relation_type,
            },
        )
        self.db.commit()
        return self._relation_view(relation)

    def list_documents(
        self,
        *,
        page: int,
        page_size: int,
        jurisdiction: str | None = None,
        knowledge_status: str | None = None,
        published_only: bool = False,
    ) -> dict[str, Any]:
        conditions = []
        if jurisdiction:
            conditions.append(GovPolicyDocument.jurisdiction == jurisdiction)
        if published_only:
            conditions.append(GovPolicyDocument.knowledge_status == PUBLISHED)
        elif knowledge_status:
            conditions.append(GovPolicyDocument.knowledge_status == knowledge_status)
        total = self.db.scalar(
            select(func.count()).select_from(GovPolicyDocument).where(*conditions)
        ) or 0
        rows = self.db.execute(
            select(GovPolicyDocument)
            .where(*conditions)
            .order_by(GovPolicyDocument.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        return {
            "items": [document_view(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def get_document(self, doc_id: str, published_only: bool = False) -> dict[str, Any]:
        doc = self._require_document(doc_id)
        if published_only and doc.knowledge_status != PUBLISHED:
            raise BusinessError("POLICY_NOT_FOUND", "政策文件不存在", 404)
        versions = self.db.execute(
            select(GovPolicyVersion)
            .where(GovPolicyVersion.doc_id == doc_id)
            .order_by(GovPolicyVersion.effective_from.desc())
        ).scalars().all()
        relations = self.db.execute(
            select(GovPolicyRelation)
            .where(
                GovPolicyRelation.from_version_id.in_(
                    [version.version_id for version in versions] or [""]
                )
            )
            .order_by(GovPolicyRelation.created_at.desc())
        ).scalars().all()
        return {
            "document": document_view(doc),
            "versions": [version_view(row) for row in versions],
            "relations": [self._relation_view(row) for row in relations],
        }

    def get_version(
        self, doc_id: str, version_label: str, published_only: bool = False
    ) -> dict[str, Any]:
        version = self._require_version(doc_id, version_label)
        doc = self._require_document(doc_id)
        if published_only and (
            doc.knowledge_status != PUBLISHED or version.review_status != REVIEWED
        ):
            raise BusinessError("POLICY_VERSION_NOT_FOUND", "政策版本不存在", 404)
        clauses = self.db.execute(
            select(GovPolicyClause)
            .where(GovPolicyClause.version_id == version.version_id)
            .order_by(GovPolicyClause.seq, GovPolicyClause.clause_no)
        ).scalars().all()
        return {
            "document": document_view(self._require_document(doc_id)),
            "version": version_view(version),
            "clauses": [clause_view(clause, version) for clause in clauses],
        }

    def applicable_versions(
        self, payload: ApplicableQueryIn, permission_max: int
    ) -> dict[str, Any]:
        stmt = (
            select(GovPolicyVersion, GovPolicyDocument)
            .join(GovPolicyDocument, GovPolicyVersion.doc_id == GovPolicyDocument.doc_id)
            .where(
                GovPolicyVersion.review_status == REVIEWED,
                GovPolicyDocument.knowledge_status == PUBLISHED,
                GovPolicyDocument.permission_level <= permission_max,
                GovPolicyDocument.jurisdiction.in_(
                    list(dict.fromkeys([payload.jurisdiction, "全国"]))
                ),
            )
        )
        if payload.document_number:
            from sqlalchemy import or_

            stmt = stmt.where(
                or_(
                    GovPolicyDocument.document_number == payload.document_number,
                    GovPolicyVersion.document_number == payload.document_number,
                )
            )
        rows = self.db.execute(stmt).all()
        applicable: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for version, doc in rows:
            state = version_state(version, payload.as_of)
            if state == "effective" and not self._subject_allowed(doc, payload.subject):
                state = "subject_not_applicable"
            item = {
                "document": document_view(doc),
                "version": version_view(version, payload.as_of),
                "state": state,
            }
            if state == "effective":
                applicable.append(item)
            else:
                excluded.append(item)
        applicable.sort(
            key=lambda item: (
                0 if item["document"]["jurisdiction"] == payload.jurisdiction else 1,
                item["document"]["title"],
            )
        )
        warnings: list[str] = []
        if len({item["document"]["doc_id"] for item in applicable}) > 1:
            warnings.append("存在多个可能适用文件，需人工核对特别规定与适用顺序")
        if not applicable:
            warnings.append("未找到该日期下已审核且处于有效期的政策版本")
        version_ids = [
            item["version"]["version_id"] for item in [*applicable, *excluded]
        ]
        relations = self.db.execute(
            select(GovPolicyRelation)
            .where(
                GovPolicyRelation.review_status == "approved",
                GovPolicyRelation.from_version_id.in_(version_ids or [""]),
            )
            .order_by(GovPolicyRelation.created_at)
        ).scalars().all()
        return {
            "as_of": payload.as_of.isoformat(),
            "jurisdiction": payload.jurisdiction,
            "subject": payload.subject,
            "applicable": applicable,
            "excluded": excluded,
            "relations": [self._relation_view(row) for row in relations],
            "manual_check_required": bool(warnings),
            "warnings": warnings,
        }

    async def build_embeddings(
        self, doc_id: str, version_label: str, actor: Actor
    ) -> dict[str, Any]:
        version = self._require_version(doc_id, version_label)
        if version.review_status != REVIEWED:
            raise BusinessError(
                "POLICY_REVIEW_STATE_INVALID", "仅已审核版本可建检索向量", 409
            )
        clauses = self.db.execute(
            select(GovPolicyClause)
            .where(GovPolicyClause.version_id == version.version_id)
            .order_by(GovPolicyClause.seq)
        ).scalars().all()
        gateway = get_model_gateway()
        model_name = embedding_model_name(gateway)
        if clauses and all(
            clause.embedding and clause.embedding_model == model_name for clause in clauses
        ):
            return {
                "version_id": version.version_id,
                "embedded": len(clauses),
                "embedding_model": model_name,
                "duplicated": True,
            }
        if not clauses:
            return {
                "version_id": version.version_id,
                "embedded": 0,
                "embedding_model": model_name,
                "duplicated": False,
            }
        vectors: list[list[float]] = []
        for start in range(0, len(clauses), EMBED_BATCH_SIZE):
            batch = clauses[start : start + EMBED_BATCH_SIZE]
            vectors.extend(await gateway.embed([clause.content for clause in batch]))
        if len(vectors) != len(clauses):
            raise BusinessError(
                "EMBEDDING_RESULT_INVALID", "向量数量与条款数量不一致", 503
            )
        for clause, vector in zip(clauses, vectors, strict=True):
            clause.embedding = vector
            clause.embedding_model = model_name
        self.db.commit()
        return {
            "version_id": version.version_id,
            "embedded": len(clauses),
            "embedding_model": model_name,
            "duplicated": False,
        }

    async def search(
        self, payload: PolicySearchIn, permission_max: int
    ) -> dict[str, Any]:
        ensure_safe_text(payload.query, payload.jurisdiction, payload.subject)
        stmt = (
            select(GovPolicyVersion, GovPolicyDocument)
            .join(GovPolicyDocument, GovPolicyVersion.doc_id == GovPolicyDocument.doc_id)
            .where(
                GovPolicyVersion.review_status == REVIEWED,
                GovPolicyDocument.knowledge_status == PUBLISHED,
                GovPolicyDocument.permission_level <= permission_max,
                GovPolicyDocument.jurisdiction.in_(
                    list(dict.fromkeys([payload.jurisdiction, "全国"]))
                ),
            )
        )
        rows = [
            (version, doc)
            for version, doc in self.db.execute(stmt).all()
            if version_state(version, payload.as_of) == "effective"
            and self._subject_allowed(doc, payload.subject)
        ]
        if not rows:
            return {"items": [], "query": payload.query, "as_of": payload.as_of.isoformat()}
        version_ids = [version.version_id for version, _ in rows]
        clauses = self.db.execute(
            select(GovPolicyClause).where(GovPolicyClause.version_id.in_(version_ids))
        ).scalars().all()
        by_version: dict[str, list[GovPolicyClause]] = {}
        for clause in clauses:
            by_version.setdefault(clause.version_id, []).append(clause)

        tokens = {
            token.strip()
            for token in jieba.lcut_for_search(payload.query)
            if len(token.strip()) >= 2 and token.strip() not in STOP_TOKENS
        }
        gateway = get_model_gateway()
        model_name = embedding_model_name(gateway)
        query_vec: list[float] | None = None
        vector_available = any(
            clause.embedding and clause.embedding_model == model_name
            for clause in clauses
        )
        warnings: list[str] = []
        if vector_available:
            try:
                query_vec = (await gateway.embed([payload.query]))[0]
            except Exception:
                warnings.append("查询向量生成失败，已退回词面检索")

        scored: list[
            tuple[float, float, GovPolicyVersion, GovPolicyDocument, GovPolicyClause]
        ] = []
        for version, doc in rows:
            metadata_hit = bool(
                payload.query.strip() in doc.title
                or (doc.document_number and doc.document_number in payload.query)
                or (
                    version.document_number
                    and version.document_number in payload.query
                )
            )
            for clause in by_version.get(version.version_id, []):
                haystack = f"{doc.title} {clause.path} {clause.content}"
                matched = [token for token in tokens if token in haystack]
                coverage = len(matched) / len(tokens) if tokens else 0.0
                vector_score = 0.0
                if (
                    query_vec is not None
                    and clause.embedding
                    and clause.embedding_model == model_name
                ):
                    vector_score = _cosine(query_vec, clause.embedding)
                if not matched and not metadata_hit and vector_score < 0.92:
                    continue
                if not metadata_hit and not matched and vector_score >= 0.92:
                    warnings.append("命中语义相似条款但缺少词面重合，需人工核对相关性")
                elif not metadata_hit and coverage < 0.5 and vector_score < 0.82:
                    continue
                score = coverage * 6.0 + max(0.0, vector_score) * 4.0
                score += sum(2.0 + min(len(token), 6) * 0.2 for token in matched)
                if (
                    doc.document_number and doc.document_number in payload.query
                ) or (
                    version.document_number
                    and version.document_number in payload.query
                ):
                    score += 10.0
                if payload.query.strip() in clause.content:
                    score += 5.0
                scored.append((round(score, 4), vector_score, version, doc, clause))
        scored.sort(key=lambda item: (-item[0], item[4].seq))
        mode = "hybrid" if query_vec is not None else "lexical"
        return {
            "query": payload.query,
            "as_of": payload.as_of.isoformat(),
            "retrieval": {
                "mode": mode,
                "embedding_model": model_name if query_vec is not None else "",
                "vector_weight": 0.4 if mode == "hybrid" else 0.0,
            },
            "warnings": list(dict.fromkeys(warnings)),
            "items": [
                {
                    "score": score,
                    "vector_score": round(vector_score, 6),
                    "document": document_view(doc),
                    "version": version_view(version, payload.as_of),
                    "clause": clause_view(clause, version),
                }
                for score, vector_score, version, doc, clause in scored[: payload.top_k]
            ],
        }

    def resolve_citations(
        self, citation_ids: list[str], permission_max: int
    ) -> dict[str, tuple[GovPolicyClause, GovPolicyVersion, GovPolicyDocument]]:
        clause_ids = [value.split("@", 1)[0] for value in citation_ids]
        rows = self.db.execute(
            select(GovPolicyClause, GovPolicyVersion, GovPolicyDocument)
            .join(GovPolicyVersion, GovPolicyClause.version_id == GovPolicyVersion.version_id)
            .join(GovPolicyDocument, GovPolicyVersion.doc_id == GovPolicyDocument.doc_id)
            .where(
                GovPolicyClause.clause_id.in_(clause_ids),
                GovPolicyVersion.review_status == REVIEWED,
                GovPolicyDocument.knowledge_status == PUBLISHED,
                GovPolicyDocument.permission_level <= permission_max,
            )
        ).all()
        return {
            f"{clause.clause_id}@{version.version_label}": (clause, version, doc)
            for clause, version, doc in rows
        }

    async def check_support(
        self, payload: PolicySupportIn, permission_max: int
    ) -> dict[str, Any]:
        ensure_safe_text(payload.claim)
        found = self.resolve_citations(payload.citation_ids, permission_max)
        missing = [cid for cid in payload.citation_ids if cid not in found]
        if missing:
            raise BusinessError(
                "POLICY_CLAUSE_NOT_FOUND",
                f"引用不存在、无权访问或未发布：{'、'.join(missing[:3])}",
                404,
            )
        evidence = [found[cid][0] for cid in payload.citation_ids]
        assessment = self._assess_claim_support(payload.claim, evidence)
        semantic_review: dict[str, Any] | None = None
        if payload.use_semantic and assessment["support_status"] != "direct":
            semantic_review = await self._semantic_support_review(
                payload.claim, evidence
            )
            if (
                semantic_review.get("status") in {"unsupported", "contradicted"}
                and assessment["support_status"] != "insufficient"
            ):
                assessment["support_status"] = "insufficient"
                assessment["support_method"] = "semantic_downgrade"
        return {
            "claim": payload.claim,
            **assessment,
            "semantic_review": semantic_review,
            "citations": [
                {
                    "citation_id": citation_id,
                    "doc_id": found[citation_id][2].doc_id,
                    "version_id": found[citation_id][1].version_id,
                    "clause_no": found[citation_id][0].clause_no,
                }
                for citation_id in payload.citation_ids
            ],
            "manual_review_required": assessment["support_status"] != "direct",
        }

    async def _semantic_support_review(
        self, claim: str, clauses: list[GovPolicyClause]
    ) -> dict[str, Any]:
        prompt = {
            "claim": claim,
            "clauses": [
                {
                    "clause_no": clause.clause_no,
                    "content": clause.content,
                }
                for clause in clauses
            ],
            "allowed_status": [
                "supported",
                "partial",
                "unsupported",
                "contradicted",
            ],
        }
        try:
            result = await get_model_gateway().chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "只判断引用条款是否支持给定论断。返回 JSON："
                            '{"status":"supported|partial|unsupported|contradicted",'
                            '"rationale":"简短依据"}。不得补写条款以外的事实。'
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                temperature=0.0,
                json_mode=True,
                timeout_s=20.0,
            )
            parsed = json.loads(result.content)
            status = str(parsed.get("status", ""))
            if status not in {"supported", "partial", "unsupported", "contradicted"}:
                raise ValueError("invalid semantic support status")
            return {
                "status": status,
                "rationale": str(parsed.get("rationale", ""))[:1000],
                "model": result.model,
                "degrade_level": result.degrade_level,
            }
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return {
                "status": "unavailable",
                "rationale": "语义复核结果不可用，保留确定性结论",
            }
        except Exception:
            logger.warning("semantic support review failed", exc_info=True)
            return {
                "status": "unavailable",
                "rationale": "语义复核服务异常，保留确定性结论",
            }

    async def interpret(
        self, payload: PolicyInterpretIn, permission_max: int
    ) -> dict[str, Any]:
        ensure_safe_text(
            payload.question, payload.jurisdiction or "", payload.subject
        )
        missing = []
        if not payload.jurisdiction:
            missing.append("政策地区")
        if payload.as_of is None:
            missing.append("事项时间")
        if missing:
            return {
                "outcome": "clarify",
                "question": payload.question,
                "clarifications": [
                    {
                        "field": field,
                        "question": f"请补充{field}，否则无法判断适用政策版本。",
                    }
                    for field in missing
                ],
                "conflicts": [],
                "claims": [],
                "citations": [],
                "warnings": ["缺少必要条件时不生成确定性政策结论"],
            }

        jurisdiction = payload.jurisdiction
        as_of = payload.as_of
        assert jurisdiction is not None and as_of is not None
        applicability = self.applicable_versions(
            ApplicableQueryIn(
                jurisdiction=jurisdiction,
                as_of=as_of,
                subject=payload.subject,
            ),
            permission_max,
        )
        applicable_doc_ids = {
            item["document"]["doc_id"] for item in applicability["applicable"]
        }
        conflicts = []
        if len(applicable_doc_ids) > 1:
            conflicts.append(
                {
                    "conflict_type": "multiple_applicable_documents",
                    "message": "存在多个可能适用文件，需人工核对特别规定与适用顺序",
                    "doc_ids": sorted(applicable_doc_ids),
                }
            )
        search = await self.search(
            PolicySearchIn(
                query=payload.question,
                jurisdiction=jurisdiction,
                subject=payload.subject,
                as_of=as_of,
                top_k=payload.top_k,
            ),
            permission_max,
        )
        items = search["items"]
        if not items:
            return {
                "outcome": "insufficient",
                "question": payload.question,
                "as_of": as_of.isoformat(),
                "clarifications": [],
                "conflicts": conflicts,
                "claims": [],
                "citations": [],
                "warnings": ["未检索到已审核且在该日期有效的条款，不能形成确定结论"],
            }

        available = {
            item["clause"]["citation_id"]: item
            for item in items
            if item["clause"].get("citation_id")
        }
        claims = []
        citations = []
        seen_citations: set[str] = set()
        for index, item in enumerate(items, start=1):
            clause = item["clause"]
            citation_id = clause["citation_id"]
            quote = clause["content"].strip()
            claim_text = (
                f"《{item['document']['title']}》"
                f"{clause['path']}{clause['clause_no']}规定：{quote}"
            )
            clause_row = self.db.get(GovPolicyClause, citation_id.split("@", 1)[0])
            support = (
                self._assess_claim_support(claim_text, [clause_row])
                if clause_row is not None
                else {
                    "support_status": "insufficient",
                    "support_method": "none",
                    "coverage": 0.0,
                    "negation_risk": False,
                }
            )
            claims.append(
                {
                    "claim_id": f"PC-{index}",
                    "claim_type": "policy_quote",
                    "text": claim_text,
                    "citation_ids": [citation_id],
                    **support,
                }
            )
            if citation_id not in seen_citations:
                seen_citations.add(citation_id)
                citations.append(
                    {
                        "citation_id": citation_id,
                        "doc_id": item["document"]["doc_id"],
                        "title": item["document"]["title"],
                        "document_number": item["version"].get("document_number", ""),
                        "version_id": item["version"]["version_id"],
                        "version_label": item["version"]["version_label"],
                        "effective_from": item["version"].get("effective_from"),
                        "effective_to": item["version"].get("effective_to"),
                        "source_url": item["version"].get("source_url", ""),
                    }
                )
        unverified = [
            claim["claim_id"]
            for claim in claims
            if not all(citation in available for citation in claim["citation_ids"])
        ]
        return {
            "outcome": "answer",
            "question": payload.question,
            "as_of": as_of.isoformat(),
            "clarifications": [],
            "retrieval": search.get("retrieval", {}),
            "summary": (
                f"按 {jurisdiction}、{as_of.isoformat()} 检索到 "
                f"{len(items)} 条有效条款。以下为条款原文摘录，不构成个案适用决定。"
            ),
            "claims": claims,
            "citations": citations,
            "conflicts": conflicts,
            "unverified_claim_ids": unverified,
            "manual_review_required": True,
            "warnings": [
                *search.get("warnings", []),
                *applicability.get("warnings", []),
                "政策解读仅供办公辅助，复杂适用关系须由有权人员复核",
            ],
        }

    def compare_versions(
        self,
        doc_id: str,
        left_label: str,
        right_label: str,
        published_only: bool = False,
    ) -> dict[str, Any]:
        left = self._require_version(doc_id, left_label)
        right = self._require_version(doc_id, right_label)
        if published_only:
            doc = self._require_document(doc_id)
            if doc.knowledge_status != PUBLISHED or any(
                version.review_status != REVIEWED for version in (left, right)
            ):
                raise BusinessError("POLICY_VERSION_NOT_FOUND", "政策版本不存在", 404)
        left_clauses = self._clause_map(left.version_id)
        right_clauses = self._clause_map(right.version_id)
        added = sorted(set(right_clauses) - set(left_clauses))
        removed = sorted(set(left_clauses) - set(right_clauses))
        changed = [
            {
                "clause_no": number,
                "left": left_clauses[number],
                "right": right_clauses[number],
            }
            for number in sorted(set(left_clauses) & set(right_clauses))
            if left_clauses[number] != right_clauses[number]
        ]
        return {
            "doc_id": doc_id,
            "left": version_view(left),
            "right": version_view(right),
            "summary": {
                "added": len(added),
                "removed": len(removed),
                "changed": len(changed),
                "unchanged": len(set(left_clauses) & set(right_clauses)) - len(changed),
            },
            "added": added,
            "removed": removed,
            "changed": changed,
        }

    # ---------------- 内部工具 ----------------
    def _assert_same_document(
        self, doc: GovPolicyDocument, payload: PolicyImportIn
    ) -> None:
        incoming = payload.document
        expected = (
            doc.title,
            doc.issuing_authority,
            doc.jurisdiction,
        )
        actual = (
            incoming.title,
            incoming.issuing_authority,
            incoming.jurisdiction,
        )
        if expected != actual:
            raise BusinessError("POLICY_DOCUMENT_CONFLICT", "文件元数据与既有记录不一致", 409)

    def _build_clauses(
        self, version_id: str, payload: PolicyImportIn
    ) -> list[GovPolicyClause]:
        seen: set[str] = set()
        clauses: list[GovPolicyClause] = []
        for index, item in enumerate(payload.clauses):
            if item.clause_no in seen:
                raise BusinessError(
                    "POLICY_CLAUSE_DUPLICATE", f"条款编号重复：{item.clause_no}", 400
                )
            seen.add(item.clause_no)
            clause_id = "CL-" + _sha256(f"{version_id}|{item.clause_no}")[:32]
            clauses.append(
                GovPolicyClause(
                    clause_id=clause_id,
                    version_id=version_id,
                    clause_no=item.clause_no,
                    path=item.path,
                    content=item.content,
                    conditions=item.conditions,
                    exceptions=item.exceptions,
                    seq=item.seq or index + 1,
                    meta=item.meta,
                )
            )
        return clauses

    def _build_relations(
        self, version_id: str, payload: PolicyImportIn, actor: Actor
    ) -> list[GovPolicyRelation]:
        relations = []
        for item in payload.relations:
            relation_id = "REL-" + _sha256(
                f"{version_id}|{item.to_version_id}|{item.relation_type}"
            )[:32]
            relations.append(
                GovPolicyRelation(
                    relation_id=relation_id,
                    from_version_id=version_id,
                    to_version_id=item.to_version_id,
                    relation_type=item.relation_type,
                    basis=item.basis,
                    review_status="draft",
                    created_by=actor.user_id,
                )
            )
        return relations

    def _add_operation(
        self,
        idempotency_key: str,
        request_hash: str,
        payload: PolicyImportIn,
        actor: Actor,
    ) -> None:
        self.db.add(
            GovPolicyImportOperation(
                operation_id=f"POP-{uuid.uuid4().hex[:24]}",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                doc_id=payload.document.doc_id,
                version_id=_version_id(
                    payload.document.doc_id, payload.version.version_label
                ),
                created_by=actor.user_id,
            )
        )

    def _import_result(
        self, doc_id: str, version_id: str, *, duplicated: bool
    ) -> dict[str, Any]:
        version = self.db.get(GovPolicyVersion, version_id)
        return {
            "doc_id": doc_id,
            "version_id": version_id,
            "review_status": version.review_status if version else "draft",
            "duplicated": duplicated,
        }

    def _require_document(self, doc_id: str) -> GovPolicyDocument:
        row = self.db.get(GovPolicyDocument, doc_id)
        if row is None:
            raise BusinessError("POLICY_NOT_FOUND", "政策文件不存在", 404)
        return row

    def _get_version(
        self, doc_id: str, version_label: str
    ) -> GovPolicyVersion | None:
        return self.db.execute(
            select(GovPolicyVersion).where(
                GovPolicyVersion.doc_id == doc_id,
                GovPolicyVersion.version_label == version_label,
            )
        ).scalars().first()

    def _require_version(self, doc_id: str, version_label: str) -> GovPolicyVersion:
        row = self._get_version(doc_id, version_label)
        if row is None:
            raise BusinessError("POLICY_VERSION_NOT_FOUND", "政策版本不存在", 404)
        return row

    def _assess_claim_support(
        self, claim: str, clauses: list[GovPolicyClause]
    ) -> dict[str, Any]:
        normalized_claim = "".join(claim.split())
        normalized_clauses = ["".join((row.content or "").split()) for row in clauses]
        if any(text and text in normalized_claim for text in normalized_clauses):
            return {
                "support_status": "direct",
                "support_method": "exact_clause_quote",
                "coverage": 1.0,
                "negation_risk": False,
            }
        claim_tokens = {
            token.strip()
            for token in jieba.lcut_for_search(claim)
            if len(token.strip()) >= 2 and token.strip() not in STOP_TOKENS
        }
        clause_text = " ".join(row.content or "" for row in clauses)
        matched = {token for token in claim_tokens if token in clause_text}
        coverage = len(matched) / len(claim_tokens) if claim_tokens else 0.0
        negation_tokens = ("不得", "禁止", "无需", "不予", "除外", "不能")
        clause_negated = any(token in clause_text for token in negation_tokens)
        claim_negated = any(token in claim for token in negation_tokens)
        negation_risk = clause_negated != claim_negated
        if coverage >= 0.6 and not negation_risk:
            status = "partial"
            method = "term_overlap"
        else:
            status = "insufficient"
            method = "term_overlap" if matched else "none"
        return {
            "support_status": status,
            "support_method": method,
            "coverage": round(coverage, 4),
            "negation_risk": negation_risk,
            "matched_terms": sorted(matched),
        }

    def _subject_allowed(self, doc: GovPolicyDocument, subject: str) -> bool:
        if not subject:
            return True
        applicability = (doc.meta or {}).get("applicability") or {}
        subjects = applicability.get("subjects") or []
        return not subjects or any(
            str(item) in subject or subject in str(item) for item in subjects
        )

    def _next_clause_seq(self, version_id: str) -> int:
        current = self.db.scalar(
            select(func.max(GovPolicyClause.seq)).where(
                GovPolicyClause.version_id == version_id
            )
        )
        return int(current or 0) + 1

    def _relation_view(self, row: GovPolicyRelation) -> dict[str, Any]:
        return {
            "relation_id": row.relation_id,
            "from_version_id": row.from_version_id,
            "to_version_id": row.to_version_id,
            "relation_type": row.relation_type,
            "basis": row.basis,
            "review_status": row.review_status,
            "reviewed_by": row.reviewed_by,
            "reviewed_at": _iso(row.reviewed_at),
        }

    def _clause_map(self, version_id: str) -> dict[str, str]:
        rows = self.db.execute(
            select(GovPolicyClause)
            .where(GovPolicyClause.version_id == version_id)
            .order_by(GovPolicyClause.seq)
        ).scalars().all()
        return {row.clause_no: row.content for row in rows}
