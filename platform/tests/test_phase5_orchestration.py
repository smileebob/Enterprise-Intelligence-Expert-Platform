"""Phase 5 验证：编排引擎端到端（澄清中断/恢复、引用装配、CS-05 合规例外、冲突标注、评估联动）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from adapters.model_gateway import OfflineModelGateway
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from core.domains import DomainRegistry
from core.knowledge.service import KnowledgeService
from core.orchestration.engine import Orchestrator
from core.orchestration.session import ChatSessionStore
from domains.chemical.ingest_adapter import build_conflicts_from_scenarios

MOCK_DIR = Path("data/mock")
KNOWLEDGE_FILES = ["products.json", "formulas.json", "processes.json", "experiment_cases.json", "external_knowledge.json"]


@pytest.fixture()
def registry():
    return DomainRegistry.load_default()


@pytest.fixture()
def seeded(db_session, registry):
    gateway = OfflineModelGateway()
    service = KnowledgeService(db_session, gateway=gateway, registry=registry)

    async def _ingest():
        for file_name in KNOWLEDGE_FILES:
            payload = json.loads((MOCK_DIR / file_name).read_text(encoding="utf-8"))
            await service.ingest("chemical", file_name, payload)
            service.write_structured("chemical", payload, file_name)

    asyncio.run(_ingest())
    conflicts_payload = json.loads((MOCK_DIR / "conflict_scenarios.json").read_text(encoding="utf-8"))
    service.repo.upsert_conflicts(build_conflicts_from_scenarios(conflicts_payload))
    db_session.commit()
    return service


@pytest.fixture()
def orchestrator(db_session, registry):
    return Orchestrator(db_session, gateway=OfflineModelGateway(), registry=registry)


@pytest.fixture()
def actor():
    return Actor(user_id="expert-1", username="工程师", role_id=Roles.EXPERT, permission_max=2)


def _new_session(db_session, domain_id="chemical"):
    store = ChatSessionStore(db_session)
    return store.create_session(domain_id, "expert-1", title="测试会话")


def _run(orchestrator, session, text, actor):
    return asyncio.run(orchestrator.run_turn(session, text, actor, message_id="MSG-TEST"))


class TestClarification:
    def test_should_interrupt_and_ask_when_slots_missing(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "帮我做个防水整理方案", actor)
        assert outcome.interrupted
        assert outcome.interrupt["kind"] == "clarify"
        assert "面料成分" in outcome.interrupt["question"]
        kinds = [e["event"] for e in outcome.events]
        assert "clarify" in kinds

    def test_should_resume_after_clarification(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        first = _run(orchestrator, session, "帮我做个防水整理方案", actor)
        assert first.interrupted
        outcome = asyncio.run(
            orchestrator.resume(session.session_id, "涤纶面料，要无氟防水，耐洗50次", actor)
        )
        assert not outcome.interrupted
        assert "防水" in outcome.text or "TF-WR" in outcome.text
        assert outcome.text
        assert "[C" in outcome.text  # 结论挂引用角标


class TestAnswerQuality:
    def test_should_answer_with_citations_and_steps(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "涤纶面料，要求柔软手感好且耐洗50次不明显下降", actor)
        assert not outcome.interrupted
        assert outcome.text
        kinds = [e["event"] for e in outcome.events]
        assert "step" in kinds and "citation" in kinds and "token" in kinds and "done" in kinds
        # 引用装配：C1..Cn 存在
        citations = [e["data"] for e in outcome.events if e["event"] == "citation"]
        assert citations and citations[0]["cid"] == "C1"
        # 结论覆盖内部产品/实验
        record_ids = {c["record_id"] for c in citations}
        assert {"TF-SS-2210"} & record_ids or {"TF-SOFT-2201"} & record_ids
        # 评估联动（选型类问题触发六维评估）
        assert outcome.state.eval_result is not None
        assert outcome.state.eval_result["total_score"] > 0

    def test_should_annotate_cs01_conflict_with_both_sides(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "客户要涤纶面料做无氟防水，要求耐洗50次不明显下降，无氟能做到吗？", actor)
        conflict_ids = {c.conflict_id for c in outcome.state.conflicts}
        assert "CS-01" in conflict_ids
        assert "冲突标注" in outcome.text
        assert "外部观点" in outcome.text
        # 双方来源均可引用
        record_ids = {c["record_id"] for c in [e["data"] for e in outcome.events if e["event"] == "citation"]}
        assert "EXT-PAP-002" in record_ids

    def test_should_trigger_cs05_compliance_notice_and_replacement(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "客户做出口欧盟的户外面料，要高性能防水，能用我们的C6有机氟TF-WR-6612吗？", actor)
        assert outcome.state.plan.get("override_flags"), "应命中 CS-05 合规例外"
        assert "合规强制提示" in outcome.text
        assert "TF-WR-6608" in outcome.text
        assert "EXT-STD-006" in outcome.text or "ZDHC" in outcome.text or "REACH" in outcome.text
        # 评估联动且 D6 被反转压分
        assert outcome.state.eval_result is not None
        assert outcome.state.eval_result["dimensions"]["D6"]["score"] <= 30

    def test_should_support_diagnostic_flow(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "客户反馈用了我们的氨基硅油后白色涤纶泛黄，是什么原因、怎么解决？", actor)
        assert "原因分析" in outcome.text
        assert "解决方案" in outcome.text
        assert {c.conflict_id for c in outcome.state.conflicts} >= {"CS-02"}

    def test_should_keep_challenge_conflict_cs04(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "网上有评测说嵌段硅油柔软度就7分，你们TF-SS-2210凭什么说9.5分？", actor)
        assert {c.conflict_id for c in outcome.state.conflicts} >= {"CS-04"}


class TestMultiTurnContext:
    """会话滑窗注入：最近消息装载 → clarify 多轮指代槽位继承（确定性规则）。"""

    def _ask(self, orchestrator, db_session, session, text, actor):
        # 镜像生产路由：先落用户消息，再驱动编排（history 装载剔除本轮）
        store = ChatSessionStore(db_session)
        message = store.add_message(session.session_id, "user", text)
        return asyncio.run(orchestrator.run_turn(session, text, actor, message_id=message.message_id))

    def test_should_inherit_slots_when_followup_is_referential(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        first = self._ask(orchestrator, db_session, session, "客户要涤纶面料做无氟防水，要求耐洗50次不明显下降", actor)
        assert not first.interrupted and first.text
        second = self._ask(orchestrator, db_session, session, "换成棉的呢？", actor)
        assert not second.interrupted, "指代轮应继承上下文而非重复澄清"
        assert second.state.need.fabric == "棉"
        assert {"防水", "无氟", "耐洗"} <= set(second.state.need.target_performance)
        inherited = second.state.plan.get("inherited_slots") or {}
        assert "目标性能" in inherited
        assert any(step.node == "context" for step in second.state.trace)
        assert any("已结合本会话上下文" in a for a in second.state.draft.annotations)
        # 检索查询并入继承槽位 → 召回延续上一轮主题（无氟防水方案而非无关品类）
        assert "TF-WR-6608" in second.text

    def test_should_inherit_market_for_compliance_continuity(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        self._ask(orchestrator, db_session, session, "涤纶面料做无氟防水，出口欧盟，耐洗30次", actor)
        second = self._ask(orchestrator, db_session, session, "换成棉的呢？", actor)
        assert second.state.need.market == "eu"
        assert second.state.need.scenario == "出口合规敏感"

    def test_should_not_override_complete_followup(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        self._ask(orchestrator, db_session, session, "涤纶面料做无氟防水，出口欧盟，耐洗30次", actor)
        second = self._ask(orchestrator, db_session, session, "锦纶面料抗静电一浴法，内销", actor)
        assert second.state.need.market == "domestic"
        assert "抗静电" in second.state.need.target_performance
        assert not second.state.plan.get("inherited_slots")

    def test_should_isolate_context_across_sessions(self, seeded, orchestrator, db_session, actor):
        first = self._ask(orchestrator, db_session, _new_session(db_session), "涤纶面料做无氟防水，耐洗30次", actor)
        assert not first.interrupted
        other = _new_session(db_session)
        second = self._ask(orchestrator, db_session, other, "换成棉的呢？", actor)
        assert second.interrupted, "新会话无滑窗可继承，指代轮仍需澄清"


class TestPersistenceAndAudit:
    def test_should_persist_assistant_message_with_citations(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        outcome = _run(orchestrator, session, "涤纶面料要防水耐洗，有没有推荐方案？", actor)
        store = ChatSessionStore(db_session)
        messages, _ = store.list_messages(session.session_id)
        assert messages and messages[0].role == "assistant"
        assert messages[0].citations
        assert messages[0].trace_id == outcome.state.turn_id

    def test_should_write_audit_entries_with_trace(self, seeded, orchestrator, db_session, actor):
        session = _new_session(db_session)
        _run(orchestrator, session, "涤纶面料要防水耐洗，有没有推荐方案？", actor)
        from core.gateway.models import GovAuditLog

        rows = db_session.query(GovAuditLog).all()
        hook_types = {r.hook_type for r in rows}
        assert {"retrieval", "output"} <= hook_types or {"retrieval"} <= hook_types
        assert any(r.decision == "pass" for r in rows)
