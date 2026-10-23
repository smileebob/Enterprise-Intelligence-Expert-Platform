"""Phase 12 扩展预留测试：①招商资质核实工具（外部系统插口）；②RERANK_MODEL 可选精排接线。

约束：不修改业务代码；fixture 构造数据；无裸 sleep；精排测试不触真实网络（离线确定性网关）。
"""

from __future__ import annotations

import asyncio

import pytest

import domains.investment.toolset  # noqa: F401 - 导入即注册 verify_qualification（@tool 装饰器）
from adapters.model_gateway import OfflineModelGateway
from app.config import get_settings
from core.abstraction.dtos import Actor, KbChunkRecord, ScoredChunk
from core.abstraction.enums import Roles
from core.abstraction.errors import ToolDeniedError
from core.domains import DomainRegistry
from core.gateway.models import GovAuditLog
from core.gateway.registry import tool_registry
from core.gateway.service import ToolCtx, ToolGateway
from core.knowledge.service import KnowledgeService
from tests.conftest import (
    ingest_chemical_knowledge,
    ingest_investment_knowledge,
    seed_users_and_permissions,
)

# ---------------- 公共 fixtures ----------------


@pytest.fixture()
def registry():
    return DomainRegistry.load_default()


@pytest.fixture()
def seeded(db_session, registry):
    """化学知识就绪（精排接线测试用）。"""
    seed_users_and_permissions(db_session)
    ingest_chemical_knowledge(db_session)
    return db_session


@pytest.fixture()
def seeded_investment(db_session, registry):
    """招商知识就绪（资质核实工具测试用）。"""
    seed_users_and_permissions(db_session)
    ingest_investment_knowledge(db_session)
    return db_session


@pytest.fixture()
def actor_expert():
    return Actor(
        user_id="u-expert",
        username="expert",
        role_id=Roles.EXPERT,
        permission_max=2,
        domain_scope=["chemical", "investment"],
    )


def _ctx(db_session, actor: Actor) -> ToolCtx:
    return ToolCtx(db=db_session, actor=actor, session_id="S-unit-tool", domain_id="investment", trace_id="T-unit-tool")


# ================================================================
# K 组：招商资质核实工具（外部核实系统预留插口）
# ================================================================
class TestVerifyQualificationTool:
    def test_should_register_contract_when_toolset_loaded(self):
        definition = tool_registry.get("verify_qualification")
        assert definition is not None
        spec = definition.spec
        assert spec.domain == "investment"
        assert spec.permission == "tool.investment.search"
        assert spec.risk_level == "normal"
        assert spec.input_schema["required"] == ["ent_id"]

    def test_should_verify_all_items_when_enterprise_qualified(self, seeded_investment, actor_expert):
        gateway = ToolGateway(seeded_investment)
        result = asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-1001"}, _ctx(seeded_investment, actor_expert)))
        assert result.ok is True
        data = result.data
        assert data["overall"] == "verified"
        assert {i["item"] for i in data["items"]} == {"工商登记", "高新技术企业认证", "融资轮次一致性"}
        for item in data["items"]:
            assert item["status"] == "verified"
            assert item["source"] and item["fetched_at"] and item["confidence"] > 0
        # 来源与获取时间标注（FR-5-04）+ 外部连接器预留说明
        assert result.meta["source_type"] == "external"
        assert result.meta["source_ref"].startswith("资质核实")
        assert result.meta["fetched_at"]
        assert "预留" in result.meta["external_connector"]

    def test_should_mark_partial_when_certification_unverified(self, seeded_investment, actor_expert):
        # ENT-1004：E1=78 < 80 → 高新认证 unverified → overall partial
        gateway = ToolGateway(seeded_investment)
        result = asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-1004"}, _ctx(seeded_investment, actor_expert)))
        by_item = {i["item"]: i["status"] for i in result.data["items"]}
        assert result.data["overall"] == "partial"
        assert by_item["高新技术企业认证"] == "unverified"
        assert by_item["工商登记"] == "verified"

    def test_should_mark_unavailable_when_profile_missing(self, seeded_investment, actor_expert):
        from domains.investment.models import InvEnterprise

        seeded_investment.add(
            InvEnterprise(
                ent_id="ENT-T99",
                name="无画像主体（模拟）",
                track="器械",
                stage="A轮",
                region="杭州",
                unified_credit_code="91330100MA2TEST99",
            )
        )
        seeded_investment.commit()
        gateway = ToolGateway(seeded_investment)
        result = asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-T99"}, _ctx(seeded_investment, actor_expert)))
        by_item = {i["item"]: i["status"] for i in result.data["items"]}
        assert by_item["融资轮次一致性"] == "unavailable"  # 无投融资记录 → 不臆测
        assert by_item["高新技术企业认证"] == "unverified"

    def test_should_return_not_found_when_entity_missing(self, seeded_investment, actor_expert):
        gateway = ToolGateway(seeded_investment)
        result = asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-NOPE"}, _ctx(seeded_investment, actor_expert)))
        assert result.ok is True
        assert result.data["overall"] == "not_found"
        assert "人工核验" in result.message

    def test_should_filter_items_and_mark_unsupported(self, seeded_investment, actor_expert):
        gateway = ToolGateway(seeded_investment)
        result = asyncio.run(
            gateway.invoke(
                "verify_qualification",
                {"ent_id": "ENT-1001", "items": ["工商登记", "环保处罚记录"]},
                _ctx(seeded_investment, actor_expert),
            )
        )
        by_item = {i["item"]: i["status"] for i in result.data["items"]}
        assert by_item["工商登记"] == "verified"
        assert by_item["环保处罚记录"] == "unsupported"  # 未覆盖项显式返回，不臆测
        assert result.data["overall"] == "partial"

    def test_should_write_audit_trail_when_invoked(self, seeded_investment, actor_expert):
        from sqlalchemy import select

        gateway = ToolGateway(seeded_investment)
        asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-1001"}, _ctx(seeded_investment, actor_expert)))
        rows = seeded_investment.execute(select(GovAuditLog)).scalars().all()
        assert any(r.action == "tool.invoke" and r.detail.get("tool") == "verify_qualification" for r in rows)

    def test_should_deny_when_role_lacks_tool_permission(self, seeded_investment):
        # 安全审计员无 tool.investment.search → 网关拦截（RBAC）
        auditor = Actor(user_id="u-auditor", username="auditor", role_id=Roles.SEC_AUDITOR, permission_max=1)
        gateway = ToolGateway(seeded_investment)
        with pytest.raises(ToolDeniedError):
            asyncio.run(gateway.invoke("verify_qualification", {"ent_id": "ENT-1001"}, _ctx(seeded_investment, auditor)))


# ================================================================
# L 组：RERANK_MODEL 可选精排接线（配置非空即启用）
# ================================================================
class _FlipRerankGateway(OfflineModelGateway):
    """确定性颠倒精排分：分数 = 索引 + 1（末位候选得最高分），用于验证重排生效。"""

    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        return [float(index + 1) for index in range(len(docs))]


class _BrokenRerankGateway(OfflineModelGateway):
    async def rerank(self, query: str, docs: list[str], *, model_alias: str = "rerank") -> list[float]:
        raise RuntimeError("rerank boom")


def _chunks() -> list[ScoredChunk]:
    return [
        ScoredChunk(
            chunk=KbChunkRecord(
                chunk_id=f"chk:t:{i}",
                doc_id="doc:t",
                seq=i,
                content=f"候选内容 {i}",
                domain_id="chemical",
                source_type="internal",
                permission_level=2,
                source_ref=f"测试/{i}",
            ),
            score=0.9 - 0.1 * i,
        )
        for i in range(3)
    ]


class TestOptionalRerankWiring:
    def test_should_keep_order_and_scores_when_rerank_disabled(self, db_session, registry, monkeypatch):
        settings = get_settings()  # 与业务代码同源实例（APP_ENV 缺省 development）
        monkeypatch.setattr(settings, "RERANK_MODEL", "")
        service = KnowledgeService(db_session, gateway=_FlipRerankGateway(), registry=registry)
        chunks = _chunks()
        result = asyncio.run(service._maybe_rerank("无氟防水", chunks))
        assert [c.chunk_id for c in result] == ["chk:t:0", "chk:t:1", "chk:t:2"]
        assert [c.score for c in result] == [0.9, 0.8, 0.7]

    def test_should_rerank_and_blend_scores_when_enabled(self, db_session, registry, monkeypatch):
        settings = get_settings()  # 与业务代码同源实例（APP_ENV 缺省 development）
        monkeypatch.setattr(settings, "RERANK_MODEL", "test-reranker")
        service = KnowledgeService(db_session, gateway=_FlipRerankGateway(), registry=registry)
        result = asyncio.run(service._maybe_rerank("无氟防水", _chunks()))
        # 反向精排（3,2,1）与混合分（0.9,0.8,0.7）各半加权 → 顺序颠倒
        assert [c.chunk_id for c in result] == ["chk:t:2", "chk:t:1", "chk:t:0"]
        assert [c.score for c in result] == [
            round(0.5 * 0.7 + 0.5 * 3.0, 6),
            round(0.5 * 0.8 + 0.5 * 2.0, 6),
            round(0.5 * 0.9 + 0.5 * 1.0, 6),
        ]

    def test_should_keep_hybrid_order_when_rerank_fails(self, db_session, registry, monkeypatch):
        settings = get_settings()  # 与业务代码同源实例（APP_ENV 缺省 development）
        monkeypatch.setattr(settings, "RERANK_MODEL", "test-reranker")
        service = KnowledgeService(db_session, gateway=_BrokenRerankGateway(), registry=registry)
        result = asyncio.run(service._maybe_rerank("无氟防水", _chunks()))
        assert [c.chunk_id for c in result] == ["chk:t:0", "chk:t:1", "chk:t:2"]
        assert [c.score for c in result] == [0.9, 0.8, 0.7]

    def test_should_apply_rerank_in_retrieve_pipeline_when_enabled(self, seeded, registry, monkeypatch):
        # 集成：同一查询，开/关精排两跑——候选集合不变、分数被融合精排分（调用点接线验证）
        query = "无氟防水 耐洗 交联剂"
        baseline_service = KnowledgeService(seeded, gateway=OfflineModelGateway(), registry=registry)
        baseline = asyncio.run(baseline_service.retrieve(query, "chemical", permission_max=2))
        baseline_scores = {e.chunk_id: e.score for e in baseline.evidence}

        settings = get_settings()  # 与业务代码同源实例（APP_ENV 缺省 development）
        monkeypatch.setattr(settings, "RERANK_MODEL", "test-reranker")
        rerank_service = KnowledgeService(seeded, gateway=_FlipRerankGateway(), registry=registry)
        reranked = asyncio.run(rerank_service.retrieve(query, "chemical", permission_max=2))
        reranked_scores = {e.chunk_id: e.score for e in reranked.evidence}

        assert set(baseline_scores) == set(reranked_scores)
        assert any(abs(baseline_scores[cid] - reranked_scores[cid]) > 1e-9 for cid in baseline_scores)
