"""Phase 17 · 06-A2 一次性审批 + 05-B effect 账本/checkpoint CAS。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from core.abstraction.dtos import Actor, ToolResult
from core.abstraction.enums import Roles
from core.abstraction.errors import ToolDeniedError
from core.gateway.registry import tool
from core.gateway.service import ToolCtx, ToolGateway
from core.orchestration import checkpoints
from core.orchestration.engine import Orchestrator
from core.orchestration.hooks import ToolIntent
from core.orchestration.state import Need, TurnState
from core.runs.approvals import (
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalService,
    args_digest,
    permission_version,
)
from core.runs.effects import EffectLedger
from core.runs.models import AgentRunEffect, ExecutionApproval
from core.runs.service import RunService

EXECUTIONS: list[str] = []


@tool(
    name="dangerous_test_action",
    description="测试危险操作（仅单测注册）",
    permission="tool.chemical.evaluate",
    domain="chemical",
    risk_level="dangerous",
    input_schema={
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    },
)
async def dangerous_test_action(ctx: ToolCtx, target: str) -> ToolResult:
    EXECUTIONS.append(target)
    return ToolResult(ok=True, data={"target": target}, meta={"source_type": "internal"})


@pytest.fixture()
def registry():
    from core.domains import DomainRegistry

    return DomainRegistry.load_default()


def _expert() -> Actor:
    return Actor(
        user_id="u-expert", username="expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"]
    )


def _admin() -> Actor:
    return Actor(
        user_id="u-admin",
        username="admin",
        role_id=Roles.PLATFORM_ADMIN,
        permission_max=2,
        domain_scope=["chemical", "investment"],
    )


def _ctx(db, *, run_id: str = "RUN-apv", approval_id: str = "") -> ToolCtx:
    return ToolCtx(
        db=db,
        actor=_expert(),
        session_id="S-apv",
        domain_id="chemical",
        trace_id="T-apv",
        run_id=run_id,
        node="tool",
        invocation_index=0,
        approval_id=approval_id,
    )


def _create_run(db) -> str:
    run = RunService(db).create_run(session_id="S-apv", owner_id="u-expert", domain_id="chemical")
    return run.run_id


def _invoke(db, run_id: str, approval_id: str = "", target: str = "TF-WR-6608", confirmed: bool = False):
    gateway = ToolGateway(db)
    return asyncio.run(
        gateway.invoke(
            "dangerous_test_action",
            {"target": target},
            _ctx(db, run_id=run_id, approval_id=approval_id),
            confirmed=confirmed,
        )
    )


class TestOneTimeApproval:
    def setup_method(self):
        EXECUTIONS.clear()

    def test_should_request_approval_instead_of_executing_dangerous_tool(self, seeded):
        run_id = _create_run(seeded)
        result = _invoke(seeded, run_id)
        assert result.meta["confirm_required"] is True
        assert result.meta["approval_id"]
        assert EXECUTIONS == []
        approval = seeded.get(ExecutionApproval, result.meta["approval_id"])
        assert approval is not None and approval.status == "pending"
        assert approval.effect_key == result.meta["effect_key"]
        assert approval.args_digest == args_digest({"target": "TF-WR-6608"})
        assert approval.permission_version == permission_version(seeded, Roles.EXPERT)
        assert approval.preview["target"] == "TF-WR-6608"

    def test_should_not_allow_confirmed_flag_to_bypass_approval(self, seeded):
        run_id = _create_run(seeded)
        result = _invoke(seeded, run_id, confirmed=True)
        assert result.meta["confirm_required"] is True
        assert EXECUTIONS == []

    def test_should_execute_once_when_approved_and_consumed(self, seeded):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        approval_id = request.meta["approval_id"]
        ApprovalService(seeded).decide(approval_id, "approve", _admin())

        result = _invoke(seeded, run_id, approval_id=approval_id)
        assert result.ok is True
        assert EXECUTIONS == ["TF-WR-6608"]
        approval = seeded.get(ExecutionApproval, approval_id)
        assert approval.status == "consumed"
        effect = seeded.get(AgentRunEffect, request.meta["effect_key"])
        assert effect is not None and effect.status == "succeeded"
        from core.runs.models import AgentRunOutbox

        outbox = seeded.execute(
            select(AgentRunOutbox).where(AgentRunOutbox.effect_key == request.meta["effect_key"])
        ).scalars().first()
        assert outbox is not None and outbox.status == "pending"

        with pytest.raises(ToolDeniedError) as exc:
            _invoke(seeded, run_id, approval_id=approval_id)
        assert exc.value.code == "APPROVAL_REJECTED"
        assert EXECUTIONS == ["TF-WR-6608"]

    def test_should_reject_dispatch_when_approval_rejected(self, seeded):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        approval_id = request.meta["approval_id"]
        ApprovalService(seeded).decide(approval_id, "reject", _admin())
        with pytest.raises(ToolDeniedError):
            _invoke(seeded, run_id, approval_id=approval_id)
        assert EXECUTIONS == []

    def test_should_reject_dispatch_when_approval_expired(self, seeded):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        approval = seeded.get(ExecutionApproval, request.meta["approval_id"])
        approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        seeded.commit()
        with pytest.raises(ApprovalExpired):
            ApprovalService(seeded).decide(approval.approval_id, "approve", _admin())
        seeded.expire_all()
        assert seeded.get(ExecutionApproval, approval.approval_id).status == "expired"

    def test_should_reject_dispatch_when_arguments_changed(self, seeded):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        approval_id = request.meta["approval_id"]
        ApprovalService(seeded).decide(approval_id, "approve", _admin())
        with pytest.raises(ToolDeniedError):
            _invoke(seeded, run_id, approval_id=approval_id, target="OTHER")
        assert EXECUTIONS == []

    def test_should_reject_approval_from_other_domain(self, seeded):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        other_domain = Actor(
            user_id="u-kbadmin", role_id=Roles.KB_ADMIN, permission_max=2, domain_scope=["investment"]
        )
        with pytest.raises(ApprovalNotFound):
            ApprovalService(seeded).decide(request.meta["approval_id"], "approve", other_domain)

    def test_should_decide_via_api_and_list_pending(self, client, seeded, admin_headers):
        run_id = _create_run(seeded)
        request = _invoke(seeded, run_id)
        pending = client.get("/api/v1/tool-approvals?domain_id=chemical", headers=admin_headers)
        assert pending.status_code == 200
        assert any(item["approval_id"] == request.meta["approval_id"] for item in pending.json()["data"])
        resp = client.post(
            f"/api/v1/tool-approvals/{request.meta['approval_id']}/decide",
            json={"action": "approve", "note": "确认执行"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "approved"
        repeat = client.post(
            f"/api/v1/tool-approvals/{request.meta['approval_id']}/decide",
            json={"action": "approve"},
            headers=admin_headers,
        )
        assert repeat.status_code == 409
        assert repeat.json()["code"] == "APPROVAL_CONFLICT"


class TestEffectLedger:
    def test_should_prepare_once_and_keep_latest_status(self, seeded):
        ledger = EffectLedger(seeded)
        first = ledger.prepare(
            "RUN-e:tool:0", run_id="RUN-e", node="tool", operation="dangerous_test_action"
        )
        second = ledger.prepare(
            "RUN-e:tool:0", run_id="RUN-e", node="tool", operation="dangerous_test_action"
        )
        assert first.effect_key == second.effect_key
        ledger.mark(first, "succeeded", result_ref="RESULT-1")
        seeded.commit()
        again = ledger.prepare(
            "RUN-e:tool:0", run_id="RUN-e", node="tool", operation="dangerous_test_action"
        )
        assert again.status == "succeeded"
        assert again.result_ref == "RESULT-1"
        count = seeded.execute(
            select(AgentRunEffect).where(AgentRunEffect.effect_key == "RUN-e:tool:0")
        ).scalars().all()
        assert len(count) == 1

    def test_should_record_solution_effect_once_for_chat_turn(self, client, seeded, db_session, expert_headers):
        session_id = client.post(
            "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
        ).json()["data"]["session_id"]
        resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "涤纶面料防水耐洗50次推荐什么方案？"},
            headers=expert_headers,
        )
        import json as _json

        done = None
        for block in resp.text.split("\n\n"):
            if block.startswith("event: done"):
                done = _json.loads(block.split("data: ", 1)[1])
        assert done is not None
        run_id = done["data"]["run_id"]
        effects = seeded.execute(
            select(AgentRunEffect).where(AgentRunEffect.run_id == run_id)
        ).scalars().all()
        solution_effects = [effect for effect in effects if effect.operation == "solution"]
        assert len(solution_effects) == 1
        assert solution_effects[0].status == "succeeded"
        assert solution_effects[0].result_ref


class TestCheckpointClaim:
    def test_should_claim_interrupted_checkpoint_only_once(self, seeded):
        state = TurnState(session_id="S-claim", turn_id="T-claim", domain_id="chemical")
        checkpoint_id = checkpoints.save_checkpoint(
            seeded, state, "clarify", status="interrupted", interrupt_kind="clarify"
        )
        first = checkpoints.claim_latest_interrupted(seeded, "S-claim")
        second = checkpoints.claim_latest_interrupted(seeded, "S-claim")
        assert first is not None and first[0] == checkpoint_id
        assert second is None


class TestEngineApprovalInterrupt:
    def test_should_interrupt_tool_node_and_then_execute_with_approval(self, seeded, registry):
        run_id = _create_run(seeded)
        orchestrator = Orchestrator(seeded, registry=registry)
        state = TurnState(
            session_id="S-apv-engine",
            turn_id="T-apv-engine",
            run_id=run_id,
            user_id="u-expert",
            role_id=Roles.EXPERT,
            domain_id="chemical",
            need=Need(raw_text="执行危险动作"),
            plan={"tool_intents": [ToolIntent("dangerous_test_action", {"target": "TF-WR-6608"}).to_dict()]},
        )

        async def noop(kind, data):
            return None

        asyncio.run(orchestrator._node_tools(state, registry.get("chemical"), _expert(), noop))
        assert state.interrupted is True
        assert state.interrupt_kind == "tool_confirm"
        approval_id = state.interrupt_payload["approval_id"]
        assert EXECUTIONS == []

        ApprovalService(seeded).decide(approval_id, "approve", _admin())
        state.interrupted = False
        state.plan["approval_id"] = approval_id
        asyncio.run(orchestrator._node_tools(state, registry.get("chemical"), _expert(), noop))
        assert state.interrupted is False
        assert EXECUTIONS == ["TF-WR-6608"]
        effect_key = state.interrupt_payload["effect_key"]
        seeded.expire_all()
        effect = seeded.get(AgentRunEffect, effect_key)
        assert effect.status == "succeeded"
