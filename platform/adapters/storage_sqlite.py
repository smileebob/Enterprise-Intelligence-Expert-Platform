"""知识仓储 · SQLite 适配器（本地开发/测试）。

向量相似度在 Python 侧计算（模拟数据规模小、可单测）；分区/权限/状态过滤仍在
SQL WHERE 完成。生产用 `storage_pgvector.PgVectorKnowledgeRepository`（SQL 下推）。
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from adapters.tokenize import tokenize
from core.abstraction.dtos import (
    ConflictClaims,
    ConflictRecord,
    KbChunkRecord,
    KbDocumentRecord,
    RetrievalQuery,
    ScoredChunk,
)
from core.knowledge.models import KbChunk, KbConflict, KbDocument


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def lexical_overlap(query_tokens: list[str], chunk_tokens: str) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    cset = set(chunk_tokens.split())
    if not cset:
        return 0.0
    hit = sum(1 for t in set(query_tokens) if t in cset)
    return hit / len(set(query_tokens))


def _chunk_to_dto(row: KbChunk) -> KbChunkRecord:
    return KbChunkRecord(
        chunk_id=row.chunk_id,
        doc_id=row.doc_id,
        seq=row.seq,
        content=row.content,
        domain_id=row.domain_id,
        source_type=row.source_type,
        category=row.category,
        confidence=float(row.confidence) if row.confidence is not None else None,
        permission_level=row.permission_level,
        source_ref=row.source_ref,
        status=row.status,
        tokens=row.tokens or "",
        embedding=row.embedding,
        embedding_model=row.embedding_model or "",
        meta=row.meta or {},
    )


def _doc_to_dto(row: KbDocument) -> KbDocumentRecord:
    return KbDocumentRecord(
        doc_id=row.doc_id,
        title=row.title,
        domain_id=row.domain_id,
        modality=row.modality,
        source_type=row.source_type,
        permission_level=row.permission_level,
        origin=row.origin,
        version=row.version,
        collected_at=row.collected_at,
        meta=row.meta or {},
    )


def _conflict_to_dto(row: KbConflict) -> ConflictRecord:
    def claims(raw: dict[str, Any]) -> ConflictClaims:
        return ConflictClaims(
            source_type=raw.get("source_type", ""),
            refs=list(raw.get("refs", [])),
            statement=raw.get("statement", ""),
            confidence=raw.get("confidence"),
        )

    return ConflictRecord(
        conflict_id=row.conflict_id,
        domain_id=row.domain_id,
        title=row.title,
        topic=row.topic,
        internal_claim=claims(row.internal_claim or {}),
        external_claim=claims(row.external_claim or {}),
        conflict_type=row.conflict_type or "",
        expected_resolution=row.expected_resolution or {},
        demo_query=row.demo_query or "",
        trap=row.trap or "",
        status=row.status,
        resolution=row.resolution,
        meta=row.meta or {},
    )


class SqlKnowledgeRepositoryBase:
    """ORM 通用知识仓储（写入路径方言无关）；检索默认在 Python 侧混合算分。

    - SQLite：直接使用本类（`SqliteKnowledgeRepository`）；
    - PostgreSQL：由 `storage_pgvector.PgVectorKnowledgeRepository` 覆写 search/upsert。
    """

    def __init__(self, db: Session):
        self.db = db

    # ---------- 文档/知识块 ----------
    def upsert_documents(self, docs: list[KbDocumentRecord]) -> None:
        for d in docs:
            row = self.db.get(KbDocument, d.doc_id)
            if row is None:
                row = KbDocument(doc_id=d.doc_id)
                self.db.add(row)
            row.title = d.title
            row.domain_id = d.domain_id
            row.modality = d.modality
            row.source_type = d.source_type
            row.permission_level = d.permission_level
            row.origin = d.origin
            row.version = d.version
            row.collected_at = d.collected_at
            row.meta = d.meta
        self.db.flush()

    def upsert_chunks(self, chunks: list[KbChunkRecord]) -> None:
        for c in chunks:
            row = self.db.get(KbChunk, c.chunk_id)
            if row is None:
                row = KbChunk(chunk_id=c.chunk_id)
                self.db.add(row)
            row.doc_id = c.doc_id
            row.seq = c.seq
            row.content = c.content
            row.tokens = c.tokens
            row.embedding = c.embedding
            row.embedding_model = c.embedding_model
            row.source_type = c.source_type
            row.category = c.category
            row.confidence = c.confidence
            row.permission_level = c.permission_level
            row.domain_id = c.domain_id
            row.source_ref = c.source_ref
            row.record_id = str(c.meta.get("record_id", "")) if c.meta else ""
            row.status = c.status
            row.meta = c.meta
        self.db.flush()

    def get_chunk(self, chunk_id: str) -> KbChunkRecord | None:
        row = self.db.get(KbChunk, chunk_id)
        return _chunk_to_dto(row) if row else None

    def get_document(self, doc_id: str) -> KbDocumentRecord | None:
        row = self.db.get(KbDocument, doc_id)
        return _doc_to_dto(row) if row else None

    # ---------- 检索 ----------
    def search(self, q: RetrievalQuery) -> list[ScoredChunk]:
        stmt = select(KbChunk).where(
            KbChunk.domain_id == q.domain_id,
            KbChunk.permission_level <= q.permission_max,
        )
        if q.status:
            stmt = stmt.where(KbChunk.status.in_(q.status))
        if q.source_types:
            stmt = stmt.where(KbChunk.source_type.in_(q.source_types))
        rows = self.db.execute(stmt).scalars().all()
        query_tokens = tokenize(q.query)
        scored: list[ScoredChunk] = []
        for row in rows:
            dto = _chunk_to_dto(row)
            vec = cosine(q.query_vec or [], dto.embedding or []) if q.query_vec else 0.0
            lex = lexical_overlap(query_tokens, dto.tokens)
            if q.query_vec and dto.embedding:
                score = q.vector_weight * max(0.0, vec) + q.lexical_weight * lex
            else:
                score = lex
            scored.append(ScoredChunk(chunk=dto, score=round(score, 6), vector_score=round(vec, 6), lexical_score=round(lex, 6)))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def chunks_by_refs(
        self,
        refs: list[str],
        domain_id: str,
        permission_max: int | None = None,
        status: list[str] | None = None,
    ) -> list[ScoredChunk]:
        if not refs:
            return []
        stmt = (
            select(KbChunk)
            .where(KbChunk.domain_id == domain_id)
            .where(or_(KbChunk.record_id.in_(refs), KbChunk.source_ref.in_(refs)))
            .order_by(KbChunk.chunk_id)
        )
        if permission_max is not None:
            stmt = stmt.where(KbChunk.permission_level <= permission_max)
        if status:
            stmt = stmt.where(KbChunk.status.in_(status))
        rows = self.db.execute(stmt).scalars().all()
        return [ScoredChunk(chunk=_chunk_to_dto(r), score=1.0) for r in rows]

    def count_chunks(self, domain_id: str, source_type: str | None = None) -> int:
        stmt = select(func.count()).select_from(KbChunk).where(KbChunk.domain_id == domain_id)
        if source_type:
            stmt = stmt.where(KbChunk.source_type == source_type)
        return int(self.db.scalar(stmt) or 0)

    # ---------- 关联（应用层遍历，FR-1-05） ----------
    def upsert_relations(self, relations: list[dict[str, Any]]) -> None:
        from core.knowledge.models import KbRelation

        for rel in relations:
            relation_id = f"{rel['from_entity']}->{rel['rel_type']}->{rel['to_entity']}"
            row = self.db.get(KbRelation, relation_id)
            if row is None:
                row = KbRelation(relation_id=relation_id)
                self.db.add(row)
            row.from_entity = rel["from_entity"]
            row.to_entity = rel["to_entity"]
            row.rel_type = rel["rel_type"]
            row.confidence = rel.get("confidence")
            row.source_type = rel.get("source_type")
            row.evidence_chunk = rel.get("evidence_chunk")
        self.db.flush()

    def related_entities(self, record_id: str, limit: int = 12) -> list[str]:
        """双向关联遍历：返回与 record_id 关联的对端实体 ID。"""
        from core.knowledge.models import KbRelation

        stmt = select(KbRelation).where(
            or_(KbRelation.from_entity == record_id, KbRelation.to_entity == record_id)
        ).limit(limit)
        rows = self.db.execute(stmt).scalars().all()
        result: list[str] = []
        for row in rows:
            other = row.to_entity if row.from_entity == record_id else row.from_entity
            if other and other != record_id and other not in result:
                result.append(other)
        return result

    # ---------- 冲突 ----------
    def list_conflicts(self, domain_id: str, status: str | None = None) -> list[ConflictRecord]:
        stmt = select(KbConflict).where(KbConflict.domain_id == domain_id)
        if status:
            stmt = stmt.where(KbConflict.status == status)
        rows = self.db.execute(stmt.order_by(KbConflict.conflict_id)).scalars().all()
        return [_conflict_to_dto(r) for r in rows]

    def get_conflict(self, conflict_id: str) -> ConflictRecord | None:
        row = self.db.get(KbConflict, conflict_id)
        return _conflict_to_dto(row) if row else None

    def upsert_conflicts(self, conflicts: list[ConflictRecord]) -> None:
        for c in conflicts:
            row = self.db.get(KbConflict, c.conflict_id)
            if row is None:
                row = KbConflict(conflict_id=c.conflict_id)
                self.db.add(row)
            row.domain_id = c.domain_id
            row.title = c.title
            row.topic = c.topic
            row.conflict_type = c.conflict_type
            row.internal_claim = {
                "source_type": c.internal_claim.source_type,
                "refs": c.internal_claim.refs,
                "statement": c.internal_claim.statement,
                "confidence": c.internal_claim.confidence,
            }
            row.external_claim = {
                "source_type": c.external_claim.source_type,
                "refs": c.external_claim.refs,
                "statement": c.external_claim.statement,
                "confidence": c.external_claim.confidence,
            }
            row.expected_resolution = c.expected_resolution
            row.demo_query = c.demo_query
            row.trap = c.trap
            row.meta = c.meta
            if c.resolution is not None:
                row.resolution = c.resolution
                row.status = c.status
        self.db.flush()

    def resolve_conflict(self, conflict_id: str, resolution: dict[str, Any]) -> None:
        row = self.db.get(KbConflict, conflict_id)
        if row is None:
            return
        row.resolution = resolution
        row.status = "resolved"
        self.db.flush()


class SqliteKnowledgeRepository(SqlKnowledgeRepositoryBase):
    """本地开发/测试用（SQLite + Python 侧向量相似度）。"""
