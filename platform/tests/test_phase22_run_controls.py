"""Phase 22 · 运行取消/事件补帧/双运行 diff/outbox 的离线切片。"""

from __future__ import annotations

import json

from sqlalchemy import select

from core.runs.models import AgentRunOutbox
from core.runs.service import RunService


def _run(seeded, *, owner: str = "u-expert", session: str = "S-ctrl") -> str:
    return RunService(seeded).create_run(session_id=session, owner_id=owner, domain_id="chemical").run_id


def _seed_events(seeded, run_id: str, *, score: float, citation: str, claims: int) -> None:
    service = RunService(seeded)
    service.append_event(run_id, "evaluation.completed", metadata={"total_score": score})
    service.append_event(
        run_id, "retrieval.completed", metadata={"citations": [{"chunk_id": citation}]}
    )
    service.append_event(run_id, "fusion.completed", metadata={"conflict_ids": ["CS-01"]})
    service.append_event(run_id, "draft.created", metadata={"claims": claims})
    service.append_event(run_id, "output.ready", metadata={"text_digest": f"digest-{score}"})
    service.append_event(run_id, "run.completed", metadata={"status": "completed"})
    service.set_status(run_id, "completed")


class TestCancel:
    def test_should_cancel_owned_run_once(self, client, seeded, expert_headers):
        run_id = _run(seeded)
        first = client.post(f"/api/v1/runs/{run_id}/cancel", headers=expert_headers)
        assert first.status_code == 200
        assert first.json()["data"]["status"] == "cancelled"
        second = client.post(f"/api/v1/runs/{run_id}/cancel", headers=expert_headers)
        assert second.status_code == 409
        assert second.json()["code"] == "STATE_INVALID"

    def test_should_hide_cancel_from_non_owner(self, client, seeded, expert_headers):
        run_id = _run(seeded, owner="u-kbadmin", session="S-other")
        resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=expert_headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == "RUN_NOT_FOUND"


class TestStreamReplay:
    def test_should_replay_only_events_after_last_event_id(self, client, seeded, expert_headers):
        run_id = _run(seeded, session="S-stream")
        service = RunService(seeded)
        service.append_event(run_id, "node.started", node="plan")
        service.append_event(run_id, "node.completed", node="plan")
        service.append_event(run_id, "node.started", node="retrieve")
        resp = client.get(
            f"/api/v1/runs/{run_id}/stream",
            headers={**expert_headers, "Last-Event-ID": f"{run_id}:2"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "id: " + run_id + ":3" in body
        assert "id: " + run_id + ":4" in body
        assert "id: " + run_id + ":2" not in body
        assert "event: replay_end" in body
        assert json.loads(body.split("event: replay_end\ndata: ")[1].split("\n")[0])["last_seq"] == 4

    def test_should_reject_malicious_last_event_id(self, client, seeded, expert_headers):
        run_id = _run(seeded, session="S-stream-2")
        resp = client.get(
            f"/api/v1/runs/{run_id}/stream",
            headers={**expert_headers, "Last-Event-ID": "RUN-other:1"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"

    def test_should_return_404_for_unknown_run_stream(self, client, seeded, expert_headers):
        resp = client.get("/api/v1/runs/RUN-nope/stream", headers=expert_headers)
        assert resp.status_code == 404


class TestDiff:
    def test_should_diff_two_runs_with_safe_summary(self, client, seeded, expert_headers):
        left = _run(seeded, session="S-diff-l")
        right = _run(seeded, session="S-diff-r")
        _seed_events(seeded, left, score=80.5, citation="A", claims=10)
        _seed_events(seeded, right, score=78.0, citation="B", claims=9)
        resp = client.get(f"/api/v1/runs/{left}/diff?against={right}", headers=expert_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["visibility"] == "common"
        assert data["differences"]["total_score"] == {"left": 80.5, "right": 78.0}
        assert data["differences"]["claims"] == {"left": 10, "right": 9}
        assert data["citations"]["left_only"] == ["A"]
        assert data["citations"]["right_only"] == ["B"]
        assert data["conflicts"]["shared"] == ["CS-01"]
        assert "payload" not in json.dumps(data)

    def test_should_reject_diff_when_second_run_not_visible(self, client, seeded, expert_headers):
        left = _run(seeded, session="S-diff-2")
        right = _run(seeded, owner="u-kbadmin", session="S-diff-3")
        resp = client.get(f"/api/v1/runs/{left}/diff?against={right}", headers=expert_headers)
        assert resp.status_code == 404


class TestOutbox:
    def test_should_enqueue_once_when_effect_dispatched(self, seeded):
        from core.runs.effects import EffectLedger
        from core.runs.outbox import OutboxService

        service = OutboxService(seeded)
        first = service.enqueue(run_id="RUN-ob", effect_key="RUN-ob:tool:0", operation="demo")
        second = service.enqueue(run_id="RUN-ob", effect_key="RUN-ob:tool:0", operation="demo")
        seeded.commit()
        assert first.outbox_id == second.outbox_id
        rows = seeded.execute(select(AgentRunOutbox)).scalars().all()
        assert len(rows) == 1 and rows[0].status == "pending"
        assert EffectLedger(seeded).get("RUN-ob:tool:0") is None
