"""Phase 21 · 04 会话摘要：确定性槽位、CAS、下一轮消费（C01/C05 切片）。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.models  # noqa: F401
from app.db import Base
from core.orchestration.engine import Orchestrator
from core.orchestration.state import Need
from core.orchestration.summary import SessionSummaryService
from tests.conftest import seed_users_and_permissions


@pytest.fixture()
def registry():
    from core.domains import DomainRegistry

    return DomainRegistry.load_default()


@pytest.fixture()
def file_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'summary.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    seed_users_and_permissions(session)
    session.close()
    yield factory
    engine.dispose()


class TestSummaryLifecycle:
    def test_should_update_summary_after_completed_turns_and_consume_next_turn(
        self, client, seeded, db_session, expert_headers, registry
    ):
        session_id = client.post(
            "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
        ).json()["data"]["session_id"]
        first = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "涤纶面料防水耐洗50次推荐什么方案？"},
            headers=expert_headers,
        )
        assert "event: done" in first.text
        summary = SessionSummaryService(db_session).get(session_id)
        assert summary is not None
        assert summary.version == 1
        assert summary.structured_slots.get("目标性能")
        assert summary.covered_message_ids

        second = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "那成本上有什么建议？"},
            headers=expert_headers,
        )
        assert "event: done" in second.text
        db_session.expire_all()
        updated = SessionSummaryService(db_session).get(session_id)
        assert updated is not None and updated.version >= 2
        assert len(updated.structured_slots["__topics__"]) <= 5

        history = Orchestrator(db_session, registry=registry)._load_history(session_id)
        assert history and history[0]["role"] == "system"
        assert "会话摘要" in history[0]["content"]

    def test_should_inherit_missing_slots_from_summary(self, file_db):
        session = file_db()
        try:
            service = SessionSummaryService(session)
            summary = service.record_turn(
                session_id="S-sum",
                owner_id="u-expert",
                domain_id="chemical",
                need=Need(
                    raw_text="涤纶无氟防水",
                    slots={"面料成分": "涤纶", "目标性能": "防水、无氟", "目标市场": "欧盟"},
                    market="eu",
                ),
                user_text="涤纶无氟防水",
                user_message_id="M1",
                assistant_message_id="M2",
            )
            assert summary is not None
            need = Need(raw_text="那耐洗呢")
            inherited = service.apply_to_need("S-sum", need)
            assert inherited["面料成分"] == "涤纶"
            assert need.target_performance == ["防水", "无氟"]
            assert need.market == "eu"
        finally:
            session.close()

    def test_should_retry_cas_when_two_sessions_update_same_summary(self, file_db):
        session_a = file_db()
        session_b = file_db()
        try:
            service_a = SessionSummaryService(session_a)
            service_b = SessionSummaryService(session_b)
            service_a.record_turn(
                session_id="S-cas",
                owner_id="u-expert",
                domain_id="chemical",
                need=Need(slots={"目标性能": "防水"}),
                user_text="第一轮",
                user_message_id="A1",
                assistant_message_id="A2",
            )
            stale = service_b.get("S-cas")  # B 持有旧版本
            assert stale.version == 1
            service_a.record_turn(
                session_id="S-cas",
                owner_id="u-expert",
                domain_id="chemical",
                need=Need(slots={"目标性能": "防水、耐洗"}),
                user_text="第二轮",
                user_message_id="A3",
                assistant_message_id="A4",
            )
            # B 用旧版本写入 → CAS 失败后应回源重试，而不是丢失消息
            result = service_b.record_turn(
                session_id="S-cas",
                owner_id="u-expert",
                domain_id="chemical",
                need=Need(slots={"目标性能": "防水、耐洗、柔软"}),
                user_text="并发轮",
                user_message_id="B1",
                assistant_message_id="B2",
            )
            assert result is not None
            assert result.version >= 2
            assert "B1" in result.covered_message_ids
            assert "A3" in result.covered_message_ids
        finally:
            session_a.close()
            session_b.close()
