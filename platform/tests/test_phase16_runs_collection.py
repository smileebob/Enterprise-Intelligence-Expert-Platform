"""Phase 16 · 05-A 运行采集/受控载荷/inspect（RP01/RP02/RP03/RP11 的离线切片）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.models  # noqa: F401 - 注册全部 ORM 模型
from app.db import Base
from core.abstraction.dtos import Actor
from core.abstraction.enums import Roles
from core.runs.models import AgentRun, AgentRunEvent, RunPayload
from core.runs.service import RunAccessDenied, RunManifest, RunService
from tests.conftest import seed_users_and_permissions


def _expert(user_id: str = "u-expert") -> Actor:
    return Actor(
        user_id=user_id, username=user_id, role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"]
    )


def _auditor() -> Actor:
    return Actor(
        user_id="u-auditor", username="auditor", role_id=Roles.SEC_AUDITOR, permission_max=1, domain_scope=["chemical"]
    )


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name = ""
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name and data is not None:
            events.append((name, data))
    return events


@pytest.fixture()
def file_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runs.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    bootstrap = factory()
    seed_users_and_permissions(bootstrap)
    bootstrap.close()
    yield factory
    engine.dispose()


class TestEventAppend:
    def test_should_allocate_contiguous_seq_and_dedupe_event_id(self, seeded):
        service = RunService(seeded)
        run = service.create_run(
            session_id="S-run-1",
            owner_id="u-expert",
            domain_id="chemical",
            manifest=RunManifest(code_version="chemical-hooks-v1", config_rev=1),
        )
        event = service.append_event(run.run_id, "node.started", node="plan", metadata={"summary": "start"})
        duplicate = service.append_event(
            run.run_id, "node.started", node="plan", event_id=event.event_id
        )
        assert duplicate.event_id == event.event_id
        service.append_event(run.run_id, "node.completed", node="plan")
        rows = (
            seeded.execute(
                select(AgentRunEvent).where(AgentRunEvent.run_id == run.run_id).order_by(AgentRunEvent.seq)
            )
            .scalars()
            .all()
        )
        assert [row.seq for row in rows] == [1, 2, 3]
        assert service.verify_chain(run.run_id)["valid"] is True
        assert seeded.get(AgentRun, run.run_id).last_seq == 3

    def test_should_retry_cas_when_another_session_appended_first(self, file_db):
        session_a = file_db()
        session_b = file_db()
        try:
            service_a = RunService(session_a)
            run = service_a.create_run(session_id="S-cas", owner_id="u-expert", domain_id="chemical")
            service_b = RunService(session_b)
            service_b.append_event(run.run_id, "node.started", node="retrieve", metadata={"summary": "B"})
            # A 的内存投影仍为 last_seq=1，CAS 失败后应回源并推进到 3
            service_a.append_event(run.run_id, "node.completed", node="retrieve", metadata={"summary": "A"})
            session_a.expire_all()
            seqs = [
                row.seq
                for row in session_a.execute(
                    select(AgentRunEvent).where(AgentRunEvent.run_id == run.run_id).order_by(AgentRunEvent.seq)
                ).scalars()
            ]
            assert seqs == [1, 2, 3]
        finally:
            session_a.close()
            session_b.close()

    def test_should_flag_unsupported_event_schema_version(self, seeded):
        service = RunService(seeded)
        run = service.create_run(session_id="S-schema", owner_id="u-expert", domain_id="chemical")
        service.append_event(run.run_id, "node.started", node="plan")
        row = seeded.execute(
            select(AgentRunEvent).where(AgentRunEvent.run_id == run.run_id, AgentRunEvent.seq == 2)
        ).scalars().first()
        row.schema_version = "999"
        seeded.commit()
        result = service.inspect(run.run_id, _expert())
        assert result["compatible"] is False
        assert "999" in result["unsupported_schema_versions"]


class TestPayloads:
    def test_should_encrypt_payload_and_block_after_delete(self, seeded):
        service = RunService(seeded)
        run = service.create_run(session_id="S-payload", owner_id="u-expert", domain_id="chemical")
        secret = {"content": "客户配方机密：无氟防水剂 12%"}
        event = service.append_event(
            run.run_id,
            "retrieval.completed",
            node="retrieve",
            payload=secret,
            classification="sensitive",
            metadata={"summary": "检索"},
        )
        row = seeded.get(RunPayload, event.payload_ref)
        assert row is not None and row.deletion_state == "available"
        raw_file = (Path(service.store.root) / row.storage_key).read_bytes()
        assert "无氟防水剂".encode() not in raw_file
        assert service.store.load(row.opaque_id) == json.dumps(
            secret, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()

        assert service.delete_payload(row.opaque_id, _expert()) is True
        with pytest.raises(KeyError):
            service.load_payload(row.opaque_id, _expert())
        view = service.inspect(run.run_id, _expert())
        assert view["integrity"]["valid"] is True
        target = next(item for item in view["timeline"] if item["seq"] == event.seq)
        assert target["payload_available"] is False

    def test_should_deny_payload_delete_for_non_owner(self, seeded):
        service = RunService(seeded)
        run = service.create_run(session_id="S-payload-2", owner_id="u-expert", domain_id="chemical")
        event = service.append_event(run.run_id, "draft.created", node="generate", payload={"text": "x"})
        with pytest.raises(RunAccessDenied):
            service.delete_payload(event.payload_ref, _auditor())


class TestInspectPermissions:
    def test_should_allow_owner_and_auditor_but_deny_unrelated_expert(self, seeded):
        service = RunService(seeded)
        run = service.create_run(session_id="S-view", owner_id="u-expert", domain_id="chemical")
        service.append_event(run.run_id, "node.started", node="plan")
        assert service.inspect(run.run_id, _expert())["integrity"]["valid"] is True
        assert service.inspect(run.run_id, _auditor())["run"]["run_id"] == run.run_id
        with pytest.raises(RunAccessDenied):
            service.inspect(run.run_id, _expert("u-other-expert"))

    def test_should_deny_inspect_when_domain_outside_scope(self, seeded):
        service = RunService(seeded)
        run = service.create_run(session_id="S-view-2", owner_id="u-expert", domain_id="chemical")
        cross_domain = Actor(
            user_id="u-kbadmin",
            role_id=Roles.KB_ADMIN,
            permission_max=2,
            domain_scope=["investment"],
        )
        with pytest.raises(RunAccessDenied):
            service.inspect(run.run_id, cross_domain)


class TestChatRunCollection:
    def test_should_collect_chat_run_and_expose_safe_inspect(
        self, client, seeded, db_session, expert_headers, auditor_headers
    ):
        session_id = client.post(
            "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
        ).json()["data"]["session_id"]
        resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "涤纶面料防水耐洗推荐什么方案？"},
            headers=expert_headers,
        )
        done = next(data for name, data in _parse_sse(resp.text) if name == "done")
        run_id = done["data"]["run_id"]
        assert run_id

        run = db_session.execute(
            select(AgentRun).where(AgentRun.run_id == run_id)
        ).scalars().first()
        assert run is not None and run.status == "completed"
        assert run.manifest["event_schema_version"] == "1"
        assert run.manifest["plugin_revision"] >= 1

        inspect = client.get(f"/api/v1/runs/{run_id}/inspect", headers=expert_headers)
        assert inspect.status_code == 200, inspect.text
        body = inspect.json()["data"]
        assert body["integrity"]["valid"] is True
        event_types = {item["event_type"] for item in body["timeline"]}
        assert {
            "run.started",
            "node.started",
            "node.completed",
            "need.extracted",
            "retrieval.completed",
            "model.requested",
            "model.returned",
            "output.ready",
            "run.completed",
        } <= event_types
        assert all("payload" not in item for item in body["timeline"])

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=expert_headers).json()["data"]
        assert events["items"]
        assert any(item["payload_available"] for item in events["items"])
        assert "content" not in events["items"][0]

        # 审计员可查看时间线（元数据），但事件中不包含原始载荷
        audit = client.get(f"/api/v1/runs/{run_id}/inspect", headers=auditor_headers)
        assert audit.status_code == 200

    def test_should_return_404_when_run_not_visible(self, client, seeded, expert_headers):
        resp = client.get("/api/v1/runs/RUN-does-not-exist", headers=expert_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "RUN_NOT_FOUND"

    def test_should_resume_waiting_run_instead_of_creating_new(self, client, seeded, db_session, expert_headers):
        session_id = client.post(
            "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
        ).json()["data"]["session_id"]
        first = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "帮我做防水整理"},
            headers=expert_headers,
        )
        clarify = next(data for name, data in _parse_sse(first.text) if name == "clarify")
        assert clarify
        run = db_session.execute(
            select(AgentRun).where(AgentRun.session_id == session_id).order_by(AgentRun.created_at.desc())
        ).scalars().first()
        assert run is not None and run.status == "waiting_input"
        resume = client.post(
            f"/api/v1/chat/sessions/{session_id}/resume",
            json={"value": "涤纶面料，无氟防水，耐洗50次"},
            headers=expert_headers,
        )
        assert resume.status_code == 200
        db_session.expire_all()
        run_after = db_session.get(AgentRun, run.run_id)
        assert run_after.status == "completed"
        count = db_session.execute(
            select(func.count()).select_from(AgentRun).where(AgentRun.session_id == session_id)
        ).scalar()
        assert count == 1, "resume 应复用等待中的运行，而不是新建"
