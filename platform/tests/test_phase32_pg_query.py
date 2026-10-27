"""Phase 32 · PG 全文查询构造：与索引侧分词一致，使用 plainto_tsquery。"""

from __future__ import annotations

import re

from adapters.storage_pgvector import PgVectorKnowledgeRepository
from adapters.tokenize import tokenize
from core.abstraction.dtos import RetrievalQuery


class _Result:
    def all(self):
        return []


class _CapturingDb:
    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, statement, params=None):
        self.statement = str(statement)
        self.params = params
        return _Result()


class TestPgLexicalQuery:
    def test_should_tokenize_chinese_query_and_use_plainto_tsquery(self):
        db = _CapturingDb()
        repo = PgVectorKnowledgeRepository(db)
        query = "邻苯二甲酸酯替代方案？（含标点）"

        assert repo.search(RetrievalQuery(query=query, domain_id="chemical")) == []

        expected = " ".join(tokenize(query))
        assert db.params["ts_query"] == expected
        assert expected != query
        assert "plainto_tsquery('simple', :ts_query)" in db.statement
        assert re.search(r"(?<!plain)to_tsquery\(", db.statement) is None

    def test_should_not_pass_raw_punctuation_to_tsquery(self):
        db = _CapturingDb()
        repo = PgVectorKnowledgeRepository(db)
        query = "？、（）"

        assert repo.search(RetrievalQuery(query=query, domain_id="chemical")) == []

        assert db.params is None
        assert db.statement is None

    def test_should_keep_vector_score_when_lexical_tokens_are_empty(self):
        db = _CapturingDb()
        repo = PgVectorKnowledgeRepository(db)
        query = "？、（）"
        query_vec = [0.1, 0.2, 0.3]

        repo.search(
            RetrievalQuery(query=query, domain_id="chemical", query_vec=query_vec)
        )

        assert db.params["ts_query"] == ""
        assert db.params["query_vec"] == query_vec
        assert "plainto_tsquery" not in db.statement
        assert "CAST(:query_vec AS vector)" in db.statement
