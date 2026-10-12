"""pg vector and fulltext indexes

Revision ID: ddbd7cabd7ed
Revises: d3aa0e523c54
Create Date: 2026-10-11 12:12:08.821795+00:00

仅 PostgreSQL 生效（技术方案 04 §4.3）：HNSW 向量索引、GIN 全文索引、组合范围索引、审计 trace 索引。
"""

from __future__ import annotations

from alembic import op

revision = "ddbd7cabd7ed"
down_revision = "d3aa0e523c54"
branch_labels = None
depends_on = None


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_pg():  # SQLite 走 Python 侧算分，无需索引
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_embedding ON kb_chunk "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_tsv ON kb_chunk USING gin (content_tsv)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_scope ON kb_chunk (domain_id, source_type, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_trace ON gov_audit_log (trace_id, ts DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mem_owner_layer ON mem_item (owner_id, layer, status)")


def downgrade() -> None:
    if not _is_pg():
        return
    for name in (
        "idx_chunk_embedding",
        "idx_chunk_tsv",
        "idx_chunk_scope",
        "idx_audit_trace",
        "idx_mem_owner_layer",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
