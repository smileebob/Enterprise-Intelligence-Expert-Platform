"""Phase 1 验证：ORM 建表 / 知识仓储 / 记忆仓储 / 评估仓储 / 离线网关 / 契约测试。"""

from __future__ import annotations

import pytest

from adapters.model_gateway import OfflineModelGateway
from adapters.repos_sql import SqlAuditRepository, SqlEvalRepository, SqlMemoryRepository
from adapters.storage_sqlite import SqliteKnowledgeRepository, cosine, lexical_overlap
from adapters.tokenize import tokenize
from core.abstraction.dtos import (
    AuditEntry,
    ConflictClaims,
    ConflictRecord,
    Evidence,
    KbChunkRecord,
    KbDocumentRecord,
    MemoryItemRecord,
    RetrievalQuery,
)


def _seed_knowledge(repo: SqliteKnowledgeRepository, emb: list[list[float]] | None = None):
    repo.upsert_documents(
        [
            KbDocumentRecord(doc_id="doc:products:TF-WR-6608", title="无氟防水剂", domain_id="chemical", source_type="internal"),
            KbDocumentRecord(doc_id="doc:external:EXT-PAP-002", title="无氟耐洗局限", domain_id="chemical", source_type="external"),
        ]
    )
    chunks = [
        KbChunkRecord(
            chunk_id="chk:product:TF-WR-6608",
            doc_id="doc:products:TF-WR-6608",
            seq=0,
            content="TF-WR-6608 无氟防水剂，50次洗涤后防水等级保持80，耐洗保持率85%。",
            domain_id="chemical",
            source_type="internal",
            confidence=0.9,
            permission_level=2,
            source_ref="内部产品库/TF-WR-6608技术说明书",
            status="ready",
            tokens="TF-WR-6608 无氟 防水剂 耐洗 保持率".replace(" ", " "),
            embedding=emb[0] if emb else None,
            meta={"record_id": "TF-WR-6608"},
        ),
        KbChunkRecord(
            chunk_id="chk:external:EXT-PAP-002",
            doc_id="doc:external:EXT-PAP-002",
            seq=0,
            content="行业普遍结论：无氟防水剂30次洗涤后保持率低于65%。",
            domain_id="chemical",
            source_type="external",
            confidence=0.65,
            permission_level=1,
            source_ref="外部文献库/EXT-PAP-002",
            status="ready",
            tokens="无氟 防水剂 耐洗 洗涤 保持率 65%",
            embedding=emb[1] if emb else None,
            meta={"record_id": "EXT-PAP-002"},
        ),
    ]
    repo.upsert_chunks(chunks)
    repo.db.commit()


class TestKnowledgeRepo:
    def test_should_upsert_and_search_when_keyword_matches(self, db_session):
        repo = SqliteKnowledgeRepository(db_session)
        _seed_knowledge(repo)
        results = repo.search(RetrievalQuery(query="无氟防水耐洗", domain_id="chemical", permission_max=1))
        assert results, "应命中包含‘无氟/防水/耐洗’的知识块"
        assert {r.chunk_id for r in results} >= {"chk:external:EXT-PAP-002"}
        # 权限过滤：permission_level=2 的内部块不出现在 permission_max=1 的结果中
        assert all(r.chunk.permission_level <= 1 for r in results)

    def test_should_filter_by_source_type_when_partition_requested(self, db_session):
        repo = SqliteKnowledgeRepository(db_session)
        _seed_knowledge(repo)
        internal = repo.search(
            RetrievalQuery(query="无氟防水", domain_id="chemical", permission_max=2, source_types=["internal"])
        )
        assert all(r.chunk.source_type == "internal" for r in internal)
        assert internal

    def test_should_rank_by_vector_when_embedding_present(self, db_session):
        gateway = OfflineModelGateway(dim=64)
        import asyncio

        vecs = asyncio.run(gateway.embed(["无氟防水耐洗保持率85%", "行业普遍结论无氟耐洗低于65"]))
        repo = SqliteKnowledgeRepository(db_session)
        _seed_knowledge(repo, emb=vecs)
        results = repo.search(
            RetrievalQuery(query="无氟防水耐洗保持率", domain_id="chemical", permission_max=2, query_vec=vecs[0])
        )
        assert results[0].chunk_id == "chk:product:TF-WR-6608"

    def test_should_return_chunks_by_refs(self, db_session):
        repo = SqliteKnowledgeRepository(db_session)
        _seed_knowledge(repo)
        rows = repo.chunks_by_refs(["EXT-PAP-002"], domain_id="chemical")
        assert len(rows) == 1 and rows[0].chunk_id == "chk:external:EXT-PAP-002"

    def test_should_roundtrip_conflict(self, db_session):
        repo = SqliteKnowledgeRepository(db_session)
        conflict = ConflictRecord(
            conflict_id="CS-01",
            domain_id="chemical",
            title="无氟耐洗性",
            topic="无氟防水耐洗性",
            internal_claim=ConflictClaims(source_type="internal", refs=["EXP-2201"], statement="85%", confidence=0.9),
            external_claim=ConflictClaims(source_type="external", refs=["EXT-PAP-002"], statement="<65%", confidence=0.65),
        )
        repo.upsert_conflicts([conflict])
        repo.db.commit()
        loaded = repo.get_conflict("CS-01")
        assert loaded is not None and loaded.internal_claim.refs == ["EXP-2201"]
        repo.resolve_conflict("CS-01", {"resolution": "internal_priority", "note": "内部实测为准"})
        repo.db.commit()
        assert repo.get_conflict("CS-01").status == "resolved"

    def test_cosine_and_overlap_helpers(self):
        assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
        assert lexical_overlap(["无氟", "防水"], "无氟 防水 耐洗") == pytest.approx(1.0)
        assert lexical_overlap(["不存在"], "无氟 防水") == pytest.approx(0.0)
        assert tokenize("客户要涤纶面料做无氟防水")  # 中文分词产出非空


class TestMemoryEvalAuditRepos:
    def test_should_roundtrip_memory_item_and_events(self, db_session):
        repo = SqlMemoryRepository(db_session)
        item = MemoryItemRecord(memory_id="m1", layer="user", owner_id="u1", content="客户偏好无氟方案", domain_id="chemical")
        repo.upsert_item(item)
        repo.add_event("m1", "apply", {"origin": "test"}, "u1")
        db_session.commit()
        assert repo.get_item("m1").content == "客户偏好无氟方案"
        assert repo.list_events("m1")[0]["event_type"] == "apply"
        repo.delete_item("m1")
        db_session.commit()
        assert repo.get_item("m1") is None

    def test_should_roundtrip_eval_result(self, db_session):
        repo = SqlEvalRepository(db_session)
        from core.abstraction.dtos import EvalStandardRecord

        repo.upsert_standard(
            EvalStandardRecord(
                standard_id="std-test",
                domain_id="chemical",
                year=2026,
                object_type="solution",
                sub_domain=None,
                standard_desc="测试标准",
                dimension_weights={"D1": 30},
                scoring_rubric={},
                version="v1",
            )
        )
        repo.save_result(
            {
                "eval_id": "EV-1",
                "domain_id": "chemical",
                "object_ref": {"products": ["TF-WR-6608"]},
                "standard_id": "std-test",
                "scenario": "出口合规敏感",
                "dimension_scores": {"D1": {"score": 85.6}},
                "total_score": 83.8,
                "confidence": 0.86,
                "idempotency_key": "idem-1",
            }
        )
        db_session.commit()
        loaded = repo.get_result("EV-1")
        assert loaded["total_score"] == pytest.approx(83.8)
        assert repo.get_active_standard("chemical", "solution").standard_id == "std-test"
        assert repo.get_result_by_idempotency("idem-1")["eval_id"] == "EV-1"

    def test_should_append_and_query_audit(self, db_session):
        repo = SqlAuditRepository(db_session)
        repo.append(AuditEntry(trace_id="t1", action="tool.invoke", actor_id="u1", hook_type="tool", decision="pass"))
        db_session.commit()
        rows = repo.query(trace_id="t1")
        assert rows and rows[0]["hook_type"] == "tool"


class TestOfflineGateway:
    def test_should_embed_deterministically_and_semantically(self):
        import asyncio

        gateway = OfflineModelGateway(dim=128)
        vecs = asyncio.run(gateway.embed(["无氟防水耐洗", "无氟防水耐洗", "完全不同的话题内容"]))
        assert vecs[0] == vecs[1]
        assert cosine(vecs[0], vecs[2]) < cosine(vecs[0], vecs[0])

    def test_should_chat_offline_with_degrade_marker(self):
        import asyncio

        gateway = OfflineModelGateway()
        result = asyncio.run(gateway.chat([{"role": "user", "content": "hi"}], json_mode=True))
        assert result.degrade_level == "L1"
        assert "calibrations" in result.content


class TestEvidenceDto:
    def test_should_carry_citation_fields(self):
        ev = Evidence(chunk_id="c1", content="x", source_type="internal", confidence=0.9, citation_id="C1", source_ref="内部实验报告/EXP-2201")
        assert ev.citation_id == "C1" and ev.source_ref.endswith("EXP-2201")
