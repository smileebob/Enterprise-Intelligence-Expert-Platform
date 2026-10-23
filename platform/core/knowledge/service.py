"""知识底座门面 KnowledgeService（技术方案 03 §3.1）：入库 / 检索（分区召回+融合）/ 冲突。

分区召回顺序由领域插件 `default_partition_order` 决定（服务端决定，客户端不可指定）；
权限过滤与分区参数下推仓储实现（检索 Hook 的强制点，见 03 §3.6）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.abstraction.dtos import (
    ConflictRecord,
    FusedEvidence,
    RetrievalQuery,
    ScoredChunk,
)
from core.domains import DomainRegistry
from core.knowledge.compliance import detect_market
from core.knowledge.fusion import FusionContext, fuse_evidence
from core.knowledge.ingest import KnowledgeIngestService, get_ingest_adapter
from core.runtime import get_domain_registry, get_model_gateway, knowledge_repo

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(self, db: Session, gateway=None, registry: DomainRegistry | None = None):
        self.db = db
        self.gateway = gateway or get_model_gateway()
        self.registry = registry or get_domain_registry()
        self.repo = knowledge_repo(db)
        self.ingest_service = KnowledgeIngestService(db, self.gateway, self.registry)

    # ---------------- 入库 ----------------
    async def ingest(self, domain_id: str, source_name: str, payload: dict[str, Any], **kwargs):
        return await self.ingest_service.ingest_payload(domain_id, source_name, payload, **kwargs)

    def write_structured(self, domain_id: str, payload: dict[str, Any], source_name: str) -> int:
        adapter = get_ingest_adapter(domain_id)
        writer = getattr(adapter, "write_structured_rows", None)
        return writer(self.db, payload, source_name) if writer else 0

    # ---------------- 检索 ----------------
    async def retrieve(
        self,
        query: str,
        domain_id: str,
        *,
        permission_max: int = 1,
        k_internal: int | None = None,
        k_external: int | None = None,
        market: str | None = None,
        need: dict[str, Any] | None = None,
        top_k: int | None = None,
        query_vec: list[float] | None = None,
    ) -> FusedEvidence:
        from app.config import get_settings

        settings = get_settings()
        plugin = self.registry.require(domain_id)

        if query_vec is None:
            query_vec = await self.embed_query(query)
        parallel_keywords = plugin.routing_keywords.get("parallel", [])
        parallel_route = any(keyword in query for keyword in parallel_keywords)

        k_int = k_internal or settings.RETRIEVAL_K_INTERNAL
        k_ext = k_external or settings.RETRIEVAL_K_EXTERNAL

        internal_hits = self._search_partition(query, query_vec, domain_id, "internal", k_int, permission_max)
        internal_hits = await self._maybe_rerank(query, internal_hits)
        external_hits: list[ScoredChunk] = []
        if parallel_route or len(internal_hits) < settings.RETRIEVAL_SUFFICIENT_MIN:
            external_hits = self._search_partition(query, query_vec, domain_id, "external", k_ext, permission_max)
            external_hits = await self._maybe_rerank(query, external_hits)

        # 已审核案例受控预算：仅 ready + 授权范围内（06-B）
        confirmed_hits: list[ScoredChunk] = []
        if settings.RETRIEVAL_K_CONFIRMED > 0:
            confirmed_hits = self._search_partition(
                query, query_vec, domain_id, "confirmed", settings.RETRIEVAL_K_CONFIRMED, permission_max
            )
            confirmed_hits = await self._maybe_rerank(query, confirmed_hits)

        # 应用层关联遍历（FR-1-05）：由命中的产品展开其配方/工艺/实验关联块
        related = self._expand_relations(domain_id, internal_hits, permission_max)

        all_conflicts = self.repo.list_conflicts(domain_id, status=None)
        ctx = FusionContext(
            plugin=plugin,
            market=market or detect_market(query, (need or {}).get("target_market")),
            need=need,
            query=query,
            fetch_missing=lambda refs: self.repo.chunks_by_refs(
                refs, domain_id, permission_max=permission_max, status=["ready"]
            ),
            products=self._collect_products(
                domain_id, internal_hits + external_hits + confirmed_hits + related, need
            ),
        )
        fused = fuse_evidence(internal_hits + external_hits + confirmed_hits + related, all_conflicts, ctx)

        if top_k:
            fused.evidence = fused.evidence[:top_k]
            # 重排 citation_id 保持连续
            for idx, item in enumerate(fused.evidence, start=1):
                item.citation_id = f"C{idx}"
        return fused

    def _search_partition(
        self, query: str, query_vec: list[float] | None, domain_id: str, source_type: str, k: int, permission_max: int
    ) -> list[ScoredChunk]:
        from app.config import get_settings

        settings = get_settings()
        result = self.repo.search(
            RetrievalQuery(
                query=query,
                domain_id=domain_id,
                query_vec=query_vec,
                source_types=[source_type],
                permission_max=permission_max,
                k_internal=k,
                k_external=k,
                vector_weight=settings.HYBRID_VECTOR_WEIGHT,
                lexical_weight=settings.HYBRID_LEXICAL_WEIGHT,
            )
        )
        return result[:k]

    async def _maybe_rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        """可选精排（RERANK_MODEL 配置非空即启用）：精排分与混合分各半加权后重排候选。

        离线兜底（OfflineModelGateway.rerank）为确定性词面重合度，保证 offline/replay 可复现；
        精排不可用或返回异常时保持混合检索原序（仅记日志），不阻断主链路。
        """
        from app.config import get_settings

        settings = get_settings()
        if not settings.RERANK_MODEL or not chunks:
            return chunks
        try:
            scores = await self.gateway.rerank(query, [c.chunk.content for c in chunks])
        except Exception:
            logger.warning("rerank failed, keep hybrid order", exc_info=True)
            return chunks
        if len(scores) != len(chunks):
            logger.warning("rerank score size mismatch (%d!=%d), keep hybrid order", len(scores), len(chunks))
            return chunks
        for chunk, rerank_score in zip(chunks, scores, strict=False):
            chunk.score = round(0.5 * (chunk.score or 0.0) + 0.5 * float(rerank_score), 6)
        return sorted(chunks, key=lambda c: c.score, reverse=True)

    def _expand_relations(
        self, domain_id: str, internal_hits: list[ScoredChunk], permission_max: int, max_products: int = 2, max_related: int = 6
    ) -> list[ScoredChunk]:
        """关联遍历（kb_relation 应用层遍历，FR-1-05）：产品 →（配方/工艺/实验）→（复配产品），深度 2。

        补齐方案装配所需的关联块（如产品对应配方、配方复配的其他产品）。
        """
        products = [
            chunk for chunk in internal_hits if (chunk.chunk.meta or {}).get("record_type") == "products"
        ][:max_products]
        if not products:
            return []
        present_ids = {chunk.chunk_id for chunk in internal_hits}
        visited_entities: set[str] = set()
        targets: list[str] = []

        def collect(entity_ids: list[str]) -> list[str]:
            found: list[str] = []
            for entity_id in entity_ids:
                if not entity_id or entity_id in visited_entities:
                    continue
                visited_entities.add(entity_id)
                for related in self.repo.related_entities(entity_id):
                    if related and related not in visited_entities:
                        found.append(related)
            return found

        # 深度 1：产品直连（配方/工艺/实验）
        level1 = collect([str((p.chunk.meta or {}).get("record_id", "")) for p in products])
        targets.extend(level1)
        # 深度 2：配方/产品再展开（复配关系等）
        level2_candidates = [
            entity for entity in level1[:6] if entity.startswith(("FORM-", "TF-", "PROC-")) or entity.startswith("FORM")
        ]
        targets.extend(collect(level2_candidates))

        if not targets:
            return []
        fetched = self.repo.chunks_by_refs(targets, domain_id, permission_max=permission_max, status=["ready"])
        result: list[ScoredChunk] = []
        for chunk in fetched:
            if chunk.chunk_id in present_ids:
                continue
            record_type = str((chunk.chunk.meta or {}).get("record_type", ""))
            if record_type == "external_knowledge":
                continue  # 关联遍历仅补内部关联块；外部由分区召回负责
            chunk.score = max(chunk.score, 0.5)  # 关联块检索分置中等（最终排序仍由优先级全序主导）
            result.append(chunk)
            present_ids.add(chunk.chunk_id)
            if len(result) >= max_related:
                break
        return result

    def authorized_chunks_by_refs(self, refs: list[str], domain_id: str, actor) -> list[ScoredChunk]:
        """受控补证门面：领域 + 主体数据上限 + ready 状态同时下推仓储。

        供工具结果回填等绕过常规检索的路径使用；缺少授权主体即拒绝。
        """
        if actor is None:
            raise ValueError("补证缺少授权主体")
        return self.repo.chunks_by_refs(
            refs,
            domain_id,
            permission_max=actor.permission_max,
            status=["ready"],
        )

    async def embed_query(self, query: str) -> list[float] | None:
        try:
            vectors = await self.gateway.embed([query])
            return vectors[0] if vectors else None
        except Exception:
            # L3：语义检索不可用 → 降级纯全文（token 匹配）
            logger.warning("query embedding failed, degrade to lexical-only retrieval", exc_info=True)
            return None

    def _collect_products(self, domain_id: str, chunks: list[ScoredChunk], need: dict[str, Any] | None) -> list[dict[str, Any]]:
        """产品合规上下文（领域适配器提供）——用于 CS-05 确定性规则评估。"""
        adapter = get_ingest_adapter(domain_id)
        collector = getattr(adapter, "collect_compliance_context", None)
        if collector is None:
            return []
        record_ids = [str((c.chunk.meta or {}).get("record_id", "")) for c in chunks]
        if need:
            record_ids += [str(p) for p in need.get("product_ids", [])]
        return collector(self.db, sorted({r for r in record_ids if r}))

    # ---------------- 冲突 ----------------
    def list_conflicts(self, domain_id: str, status: str | None = None) -> list[ConflictRecord]:
        return self.repo.list_conflicts(domain_id, status)

    def get_conflict(self, conflict_id: str) -> ConflictRecord | None:
        return self.repo.get_conflict(conflict_id)

    def resolve_conflict(self, conflict_id: str, resolution: dict[str, Any]) -> None:
        self.repo.resolve_conflict(conflict_id, resolution)
        self.db.commit()

    def count_chunks(self, domain_id: str, source_type: str | None = None) -> int:
        return self.repo.count_chunks(domain_id, source_type)
