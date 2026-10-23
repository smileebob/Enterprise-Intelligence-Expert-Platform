"""双方言列类型：向量列（PostgreSQL+pgvector ↔ SQLite 文本承载）。

生产环境用 PostgreSQL 的 VECTOR(n)（HNSW 索引）；本地开发与测试用 SQLite 时以
JSON 文本承载，向量相似度在上层适配器内计算（M0 适配层职责）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR


def json_col():
    """JSON 列：PostgreSQL 用 JSONB，其余方言用 JSON。"""
    return JSON().with_variant(JSONB(), "postgresql")


class TsVectorType(TypeDecorator):
    """全文检索列：PostgreSQL 用 TSVECTOR（应用侧 jieba 预分词写入）；其余方言落空。"""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":  # pragma: no cover - 依赖外部 PG
            return dialect.type_descriptor(TSVECTOR())
        return dialect.type_descriptor(Text())


class VectorType(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, dim: int = 1024):
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":  # pragma: no cover - 依赖外部 PG
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dim))
            except ImportError:
                return dialect.type_descriptor(Text())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":  # pragma: no cover
            return list(value)
        return json.dumps([round(float(v), 8) for v in value], ensure_ascii=False)

    def process_result_value(self, value: Any, dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return [float(v) for v in json.loads(value)]
            except ValueError:
                return None
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):  # pragma: no cover
            return None
