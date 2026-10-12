"""Phase 2 验证：KP-1 入库管道 + 分级打标 + 分区召回 + 融合消解 + CS-05 合规例外（确定性规则）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.model_gateway import OfflineModelGateway
from core.domains import DomainRegistry
from core.knowledge.compliance import detect_market, evaluate_compliance_override
from core.knowledge.service import KnowledgeService
from domains.chemical.ingest_adapter import build_conflicts_from_scenarios

MOCK_DIR = Path("data/mock")
KNOWLEDGE_FILES = ["products.json", "formulas.json", "processes.json", "experiment_cases.json", "external_knowledge.json"]


@pytest.fixture()
def registry():
    return DomainRegistry.load_default()


@pytest.fixture()
def knowledge(db_session, registry):
    from app.config import get_settings

    gateway = OfflineModelGateway(dim=get_settings().EMBED_DIM)
    return KnowledgeService(db_session, gateway=gateway, registry=registry)


async def _ingest_all(knowledge: KnowledgeService):
    for file_name in KNOWLEDGE_FILES:
        payload = json.loads((MOCK_DIR / file_name).read_text(encoding="utf-8"))
        await knowledge.ingest("chemical", file_name, payload)
        knowledge.write_structured("chemical", payload, file_name)
    conflicts_payload = json.loads((MOCK_DIR / "conflict_scenarios.json").read_text(encoding="utf-8"))
    knowledge.repo.upsert_conflicts(build_conflicts_from_scenarios(conflicts_payload))
    knowledge.db.commit()


@pytest.fixture()
def seeded_knowledge(db_session, registry):
    import asyncio

    from app.config import get_settings

    gateway = OfflineModelGateway(dim=get_settings().EMBED_DIM)
    service = KnowledgeService(db_session, gateway=gateway, registry=registry)
    asyncio.run(_ingest_all(service))
    return service


class TestIngest:
    def test_should_ingest_all_knowledge_files_with_tagging(self, seeded_knowledge):
        service = seeded_knowledge
        repo = service.repo
        assert repo.count_chunks("chemical", "internal") >= 25
        assert repo.count_chunks("chemical", "external") == 8
        # 六级打标继承：内部/外部与权限等级
        chunk = repo.get_chunk("chk:products:TF-WR-6608")
        assert chunk.source_type == "internal" and chunk.permission_level == 2
        assert chunk.confidence == pytest.approx(0.9)
        assert chunk.source_ref.startswith("内部产品库/")
        ext = repo.get_chunk("chk:external_knowledge:EXT-STD-006")
        assert ext.category == "compliance"  # 法规条目打 compliance 标签（CS-05 反转依据）
        assert ext.permission_level == 1
        # 向量化完成（离线哈希嵌入）
        assert chunk.embedding and len(chunk.embedding) == 1024

    def test_should_mark_sensitive_formula_chunks(self, seeded_knowledge):
        chunk = seeded_knowledge.repo.get_chunk("chk:formulas:FORM-6608-WR")
        assert "配方配比" in (chunk.meta or {}).get("sensitive_fields", [])

    def test_should_link_conflict_refs_in_chunks(self, seeded_knowledge):
        chunk = seeded_knowledge.repo.get_chunk("chk:experiment_cases:EXP-2201")
        assert (chunk.meta or {}).get("conflict_ref") == "CS-01"

    def test_should_write_structured_rows(self, seeded_knowledge, db_session):
        from domains.chemical.models import ChemExperiment, ChemProduct

        product = db_session.get(ChemProduct, "TF-WR-6612")
        assert product is not None and product.compliance_level == "restricted_substance"
        assert db_session.get(ChemProduct, "TF-WR-6608").compliance_level == "clean"
        assert db_session.get(ChemExperiment, "EXP-2201") is not None


class TestRetrievalAndFusion:
    def test_should_recall_internal_first_with_citations(self, seeded_knowledge):
        import asyncio

        fused = asyncio.run(
            seeded_knowledge.retrieve("涤纶面料 无氟防水 耐洗50次", "chemical", permission_max=2)
        )
        assert fused.evidence, "应召回证据"
        first_citations = [e.citation_id for e in fused.evidence[:3]]
        assert first_citations == ["C1", "C2", "C3"]
        record_ids = {e.meta.get("record_id") for e in fused.evidence}
        assert {"TF-WR-6608", "EXP-2201", "FORM-6608-WR"} & record_ids

    def test_should_respect_permission_level(self, seeded_knowledge):
        import asyncio

        fused = asyncio.run(seeded_knowledge.retrieve("无氟防水 耐洗", "chemical", permission_max=1))
        assert fused.evidence
        assert all(e.meta.get("record_id") not in {"TF-WR-6608"} or e.source_type == "external" for e in fused.evidence)
        # 内部块 permission_level=2 不应出现
        assert all(e.source_type == "external" for e in fused.evidence)

    def test_should_attach_cs01_conflict_with_both_sides(self, seeded_knowledge):
        import asyncio

        fused = asyncio.run(
            seeded_knowledge.retrieve("无氟防水剂的耐洗性 50次洗涤 保持率", "chemical", permission_max=2)
        )
        conflict_ids = {c.conflict_id for c in fused.conflicts}
        assert "CS-01" in conflict_ids
        record_ids = {e.meta.get("record_id") for e in fused.evidence}
        # 双方来源均可引用：内部 EXP-2201/TF-WR-6608 与外部 EXT-PAP-002
        assert "EXT-PAP-002" in record_ids
        assert {"EXP-2201", "TF-WR-6608"} & record_ids
        cs01 = next(c for c in fused.conflicts if c.conflict_id == "CS-01")
        assert cs01.expected_resolution.get("must_note")

    def test_should_trigger_cs05_compliance_override_for_eu(self, seeded_knowledge):
        import asyncio

        query = "客户做出口欧盟的户外面料，要高性能防水，能用我们的C6有机氟TF-WR-6612吗？"
        fused = asyncio.run(seeded_knowledge.retrieve(query, "chemical", permission_max=2))
        assert fused.override_flags, "应命中 CS-05 合规例外"
        flag = fused.override_flags[0]
        assert flag["rule_id"] == "CS-05-PFAS-EU"
        assert flag["restricted_products"] == ["TF-WR-6612"]
        assert "TF-WR-6608" in flag["recommend_products"]
        record_ids = {e.meta.get("record_id") for e in fused.evidence}
        assert "EXT-STD-006" in record_ids, "合规例外须引用外部法规"
        assert any("合规例外" in n for n in fused.notes)
        # 合规类外部证据被前置（反转）
        assert fused.evidence[0].category == "compliance"

    def test_should_not_trigger_cs05_for_domestic(self, seeded_knowledge):
        import asyncio

        query = "内销涤纶高性能防水防油，成本可控，怎么配？"
        fused = asyncio.run(seeded_knowledge.retrieve(query, "chemical", permission_max=2))
        assert not fused.override_flags, "内销场景不强制合规拦截（CS-05 负向）"

    def test_should_attach_cs04_conflict(self, seeded_knowledge):
        import asyncio

        query = "网上有评测说嵌段硅油柔软度就7分，你们TF-SS-2210凭什么说9.5分？"
        fused = asyncio.run(seeded_knowledge.retrieve(query, "chemical", permission_max=2))
        assert "CS-04" in {c.conflict_id for c in fused.conflicts}
        record_ids = {e.meta.get("record_id") for e in fused.evidence}
        assert {"EXP-2210", "EXT-RPT-007"} <= record_ids


class TestComplianceRules:
    def test_detect_market(self):
        assert detect_market("出口欧盟的户外面料") == "eu"
        assert detect_market("内销订单") == "domestic"
        assert detect_market("随便聊聊") == "unknown"

    def test_override_requires_restricted_product(self):
        products = [{"product_id": "TF-WR-6608", "category": "防水剂", "sub_type": "无氟", "compliance": "无氟", "compliance_level": "clean"}]
        assert not evaluate_compliance_override("eu", products).triggered
        products.append({"product_id": "TF-WR-6612", "category": "防水剂", "sub_type": "有机氟（C6 短链）", "compliance": "C6", "compliance_level": "restricted_substance"})
        override = evaluate_compliance_override("eu", products)
        assert override.triggered and override.recommend_products == ["TF-WR-6608"]
