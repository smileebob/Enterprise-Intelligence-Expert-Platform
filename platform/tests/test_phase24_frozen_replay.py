"""Phase 24 · frozen 回放：节点快照、纯计算重放、零业务写入。"""

from __future__ import annotations

import json

from sqlalchemy import func, select

from core.orchestration.models import ChatMessage
from core.runs.models import AgentRun, AgentRunEvent
from core.runs.service import RunService


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


def _chat_run(client, seeded, expert_headers) -> str:
    session_id = client.post(
        "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
    ).json()["data"]["session_id"]
    resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"text": "涤纶面料防水耐洗50次推荐什么方案？"},
        headers=expert_headers,
    )
    done = next(data for name, data in _parse_sse(resp.text) if name == "done")
    return done["data"]["run_id"]


class TestFrozenReplay:
    def test_should_replay_generate_output_without_business_writes(
        self, client, seeded, db_session, expert_headers
    ):
        run_id = _chat_run(client, seeded, expert_headers)
        service = RunService(db_session)
        generate_cp = service.latest_checkpoint(run_id, next_node="generate")
        assert generate_cp is not None, "evaluate 后应保存 generate 快照"

        messages_before = db_session.execute(select(func.count()).select_from(ChatMessage)).scalar()
        events_before = db_session.execute(select(func.count()).select_from(AgentRunEvent)).scalar()

        resp = client.post(
            f"/api/v1/runs/{run_id}/replays", json={"mode": "frozen"}, headers=expert_headers
        )
        assert resp.status_code == 200, resp.text
        report = resp.json()["data"]
        assert report["replay_kind"] == "frozen_generate_output"
        assert report["text_match"] is True, report
        assert report["blocked"] is False
        assert report["token_events"] > 0
        assert report["no_business_writes"] is True

        db_session.expire_all()
        assert db_session.execute(select(func.count()).select_from(ChatMessage)).scalar() == messages_before
        assert db_session.execute(select(func.count()).select_from(AgentRunEvent)).scalar() == events_before

    def test_should_block_when_checkpoint_missing(self, client, seeded, expert_headers, db_session):
        run = RunService(db_session).create_run(
            session_id="S-no-cp", owner_id="u-expert", domain_id="chemical"
        )
        RunService(db_session).finish_run(run.run_id, "completed")
        resp = client.post(
            f"/api/v1/runs/{run.run_id}/replays", json={"mode": "frozen"}, headers=expert_headers
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "REPLAY_UNAVAILABLE"

    def test_should_block_when_code_version_mismatch(self, client, seeded, db_session, expert_headers):
        run_id = _chat_run(client, seeded, expert_headers)
        run = db_session.get(AgentRun, run_id)
        run.manifest = {**run.manifest, "code_version": "old-hooks-v0"}
        db_session.commit()
        resp = client.post(
            f"/api/v1/runs/{run_id}/replays", json={"mode": "frozen"}, headers=expert_headers
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "REPLAY_VERSION_MISMATCH"

    def test_should_block_when_snapshot_payload_deleted(self, client, seeded, db_session, expert_headers):
        run_id = _chat_run(client, seeded, expert_headers)
        service = RunService(db_session)
        checkpoint = service.latest_checkpoint(run_id, next_node="generate") or service.latest_checkpoint(run_id)
        assert checkpoint is not None
        service.store.delete(checkpoint.state_ref)
        db_session.commit()
        resp = client.post(
            f"/api/v1/runs/{run_id}/replays", json={"mode": "frozen"}, headers=expert_headers
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "REPLAY_UNAVAILABLE"

    def test_should_hide_replay_from_unrelated_user_and_allow_admin(
        self, client, seeded, db_session, expert_headers, admin_headers
    ):
        run = RunService(db_session).create_run(
            session_id="S-owner", owner_id="u-kbadmin", domain_id="chemical"
        )
        assert client.post(
            f"/api/v1/runs/{run.run_id}/replays", json={"mode": "frozen"}, headers=expert_headers
        ).status_code == 404
        # admin 有 run.replay 权限，但因缺快照返回 409 而不是 404（权限已通过）
        resp = client.post(
            f"/api/v1/runs/{run.run_id}/replays", json={"mode": "frozen"}, headers=admin_headers
        )
        assert resp.status_code == 409
