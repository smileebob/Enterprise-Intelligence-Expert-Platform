"""KP-1 知识管道 · 入库（技术方案 03 §3.1 关键流程 1）。

解析适配器（结构化 JSON 直导）→ 结构感知切分（一实体一块，不跨实体）→
六级打标（source_type/confidence 从数据继承）→ 敏感扫描（配方配比/报价/联系方式）→
向量化（ModelGateway，可降级）→ 索引（ready）。
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.common.context import get_trace_id
from core.abstraction.dtos import (
    ConflictRecord,
    KbChunkRecord,
    KbDocumentRecord,
)
from core.domains import DomainRegistry
from core.runtime import knowledge_repo

logger = logging.getLogger(__name__)


@dataclass
class IngestPackage:
    documents: list[KbDocumentRecord] = field(default_factory=list)
    chunks: list[KbChunkRecord] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)  # 应用层关联遍历（FR-1-05）
    stats: dict[str, Any] = field(default_factory=dict)


def get_ingest_adapter(domain_id: str):
    """领域入库适配器：domains.{domain_id}.ingest_adapter.build_ingest_package。"""
    try:
        module = importlib.import_module(f"domains.{domain_id}.ingest_adapter")
    except ImportError as exc:
        raise KeyError(f"领域 {domain_id} 未提供入库适配器") from exc
    return module


class KnowledgeIngestService:
    """入库编排：适配器解析 → 向量化 → 仓储落库（幂等 upsert）。"""

    def __init__(self, db: Session, gateway, registry: DomainRegistry):
        self.db = db
        self.gateway = gateway
        self.registry = registry

    async def ingest_payload(
        self,
        domain_id: str,
        source_name: str,
        payload: dict[str, Any],
        *,
        source_type: str | None = None,
        permission_level: int | None = None,
    ) -> IngestPackage:
        plugin = self.registry.require(domain_id)
        adapter = get_ingest_adapter(domain_id)
        package = adapter.build_ingest_package(self.db, payload, source_name)

        # 六级打标兜底：调用方可覆盖 source_type / permission_level
        for chunk in package.chunks:
            if source_type:
                chunk.source_type = source_type
            if permission_level is not None:
                chunk.permission_level = permission_level
            else:
                coll = plugin.collection_for(chunk.source_type)
                if coll:
                    chunk.permission_level = int(coll.get("permission_level", chunk.permission_level))

        await self._embed_chunks(package.chunks)

        repo = knowledge_repo(self.db)
        repo.upsert_documents(package.documents)
        repo.upsert_chunks(package.chunks)
        if package.conflicts:
            repo.upsert_conflicts(package.conflicts)
        if package.relations:
            repo.upsert_relations(package.relations)
        self.db.commit()

        package.stats.update(
            {
                "docs": len(package.documents),
                "chunks": len(package.chunks),
                "conflicts_detected": len(package.conflicts),
                "trace_id": get_trace_id(),
            }
        )
        logger.info(
            "ingest done: domain=%s source=%s docs=%s chunks=%s conflicts=%s",
            domain_id,
            source_name,
            len(package.documents),
            len(package.chunks),
            len(package.conflicts),
        )
        return package

    async def _embed_chunks(self, chunks: list[KbChunkRecord]) -> None:
        pending = [c for c in chunks if c.embedding is None and c.content]
        if not pending:
            return
        batch_size = 32
        model_name = embedding_model_name(self.gateway)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            try:
                vectors = await self.gateway.embed([c.content for c in batch])
            except Exception:
                logger.warning("embedding failed for batch, keep lexical-only", exc_info=True)
                continue
            for chunk, vec in zip(batch, vectors, strict=False):
                chunk.embedding = vec
                chunk.embedding_model = model_name


def embedding_model_name(gateway: Any) -> str:
    """网关的嵌入模型名（向量版本化字段 embedding_model 用）。"""
    for attr in ("embed_model_name", "embed_model"):
        value = getattr(gateway, attr, None)
        if value:
            return str(value)
    primary = getattr(gateway, "primary", None)
    if primary is not None:
        return embedding_model_name(primary)
    return "unknown-embed"


# ---------------- 敏感扫描（配方配比/报价/联系方式） ----------------
SENSITIVE_FIELD_RULES: list[tuple[str, list[str]]] = [
    ("配方配比", ["composition", "配方", "配比", "amount_g_per_L"]),
    ("报价", ["报价", "价格", "单价", "price", "quote"]),
    ("客户联系方式", ["联系方式", "电话", "手机", "邮箱", "email", "phone"]),
]


def scan_sensitive(text: str, structured: dict[str, Any] | None = None) -> list[str]:
    """敏感字段扫描：返回命中的敏感类型列表（BR-08 脱敏规则表依据）。"""
    blob = text or ""
    if structured:
        blob += " " + str(structured)
    hits: list[str] = []
    for field_type, markers in SENSITIVE_FIELD_RULES:
        if any(marker in blob for marker in markers):
            hits.append(field_type)
    return hits
