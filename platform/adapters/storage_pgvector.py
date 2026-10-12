"""知识仓储 · PostgreSQL 16 + pgvector 适配器（生产/Compose 一键起栈）。

检索 SQL 下推：向量（HNSW `<=>`）+ 全文（tsvector `ts_rank`）+ 标量过滤（分区/权限/状态）
在同一条 SQL 完成；`content_tsv` 由应用侧 jieba 预分词写入（`to_tsvector('simple', tokens)`）。
绑定参数一律参数化（规则 D-02/I-01）；本模块依赖外部 PG，本地无 PG 时不被引用。
"""

from __future__ import annotations

from sqlalchemy import text

from adapters.storage_sqlite import SqlKnowledgeRepositoryBase, _chunk_to_dto
from core.abstraction.dtos import KbChunkRecord, RetrievalQuery, ScoredChunk


class PgVectorKnowledgeRepository(SqlKnowledgeRepositoryBase):
    def upsert_chunks(self, chunks: list[KbChunkRecord]) -> None:  # pragma: no cover - 依赖外部 PG
        super().upsert_chunks(chunks)
        # 应用侧预分词写入 tsv（'simple' 配置，零额外分词服务）
        for c in chunks:
            if c.tokens:
                self.db.execute(
                    text("UPDATE kb_chunk SET content_tsv = to_tsvector('simple', :tokens) WHERE chunk_id = :cid"),
                    {"tokens": c.tokens, "cid": c.chunk_id},
                )
        self.db.flush()

    def search(self, q: RetrievalQuery) -> list[ScoredChunk]:  # pragma: no cover - 依赖外部 PG
        conditions = ["domain_id = :domain_id", "permission_level <= :permission_max"]
        params: dict = {
            "domain_id": q.domain_id,
            "permission_max": q.permission_max,
            "vec_w": q.vector_weight,
            "lex_w": q.lexical_weight,
            "k": max(q.k_internal, q.k_external),
        }
        if q.status:
            conditions.append("status = ANY(:status)")
            params["status"] = list(q.status)
        if q.source_types:
            conditions.append("source_type = ANY(:source_types)")
            params["source_types"] = list(q.source_types)

        ts_query = " | ".join(t for t in q.query.split() if t) or ""
        params["ts_query"] = ts_query
        if q.query_vec:
            params["query_vec"] = q.query_vec
            score_expr = (
                "(:vec_w * GREATEST(0, 1 - (embedding <=> CAST(:query_vec AS vector)))"
                " + :lex_w * COALESCE(ts_rank(content_tsv, to_tsquery('simple', :ts_query)), 0))"
            )
        else:
            score_expr = "COALESCE(ts_rank(content_tsv, to_tsquery('simple', :ts_query)), 0)"

        sql = text(
            f"""
            SELECT chunk_id, ({score_expr}) AS score
            FROM kb_chunk
            WHERE {' AND '.join(conditions)}
            ORDER BY score DESC
            LIMIT :k
            """  # noqa: S608  # nosec B608 - 仅白名单条件与静态表达式片段拼接，值全部参数绑定（规则 D-02/I-01 已复核）
        )
        rows = self.db.execute(sql, params).all()
        result: list[ScoredChunk] = []
        for chunk_id, score in rows:
            dto = self.get_chunk(chunk_id)
            if dto is not None:
                result.append(ScoredChunk(chunk=dto, score=float(score or 0.0)))
        return result


def to_chunk_dto(row) -> KbChunkRecord:  # 兼容导出
    return _chunk_to_dto(row)
