"""Phase 20 · 06-C 输出审核动作/持久化/摘要绑定（O01/O02 的切片）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from core.abstraction.dtos import Actor, ConflictClaims, ConflictRecord, Evidence
from core.abstraction.enums import Roles
from core.guardrails.hooks import HookContext
from core.guardrails.output_hook import OutputGuard
from core.orchestration.engine import Orchestrator
from core.orchestration.models import ChatMessage
from core.orchestration.state import Claim, Draft, DraftSection, TurnState
from core.runs.models import OutputReview
from core.runs.output_reviews import (
    OutputReviewConflict,
    OutputReviewService,
    draft_digest,
)
from core.runs.service import RunService


@pytest.fixture()
def registry():
    from core.domains import DomainRegistry

    return DomainRegistry.load_default()


def _expert() -> Actor:
    return Actor(
        user_id="u-expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"]
    )


def _admin() -> Actor:
    return Actor(
        user_id="u-admin", role_id=Roles.PLATFORM_ADMIN, permission_max=2, domain_scope=["chemical"]
    )


def _evidence(citation: str = "C1", record_id: str = "P1") -> list[Evidence]:
    return [
        Evidence(
            chunk_id=f"chk:{record_id}",
            content="证据正文",
            source_type="internal",
            confidence=0.9,
            citation_id=citation,
            meta={"record_id": record_id},
        )
    ]


def _draft(*, citation: str = "C1", confidence: float = 0.9) -> Draft:
    return Draft(
        summary="方案摘要",
        sections=[DraftSection(title="推荐方案", claims=[Claim("推荐方案结论", [citation])])],
        citations=[{"cid": citation}],
        confidence=confidence,
    )


def _ctx() -> HookContext:
    return HookContext(stage="output", actor=_expert(), domain_id="chemical", session_id="S-review")


class TestGuardAction:
    def test_should_review_when_invalid_citation_exists(self, seeded):
        check = OutputGuard(seeded).check(_draft(citation="C999"), _evidence("C1"), _ctx())
        assert check.action == "review"
        assert check.review_reasons
        assert "C999" in check.invalid_citations

    def test_should_review_when_conflict_only_one_side_has_visible_evidence(self, seeded):
        draft = _draft()
        draft.conflicts = [
            ConflictRecord(
                conflict_id="CS-X",
                domain_id="chemical",
                title="单边冲突",
                topic="测试",
                internal_claim=ConflictClaims(source_type="internal", refs=["P1"], statement="内部"),
                external_claim=ConflictClaims(source_type="external", refs=["P2"], statement="外部"),
            )
        ]
        check = OutputGuard(seeded).check(draft, _evidence("C1", "P1"), _ctx())
        assert check.action == "review"
        assert any("仅一方" in reason for reason in check.review_reasons)

    def test_should_allow_valid_output_and_annotate_unknown_confidence(self, seeded):
        allowed = OutputGuard(seeded).check(_draft(), _evidence("C1"), _ctx())
        assert allowed.action == "allow"
        unknown = OutputGuard(seeded).check(_draft(confidence=0.0), _evidence("C1"), _ctx())
        assert unknown.action == "annotate"
        assert any("置信度未知" in note for note in unknown.annotations)


class TestOutputReviewFlow:
    def test_should_stop_before_tokens_and_persist_review_when_action_is_review(self, seeded, registry):
        run = RunService(seeded).create_run(
            session_id="S-review", owner_id="u-expert", domain_id="chemical"
        )
        orchestrator = Orchestrator(seeded, registry=registry)
        state = TurnState(
            session_id="S-review",
            turn_id="T-review",
            run_id=run.run_id,
            user_id="u-expert",
            role_id=Roles.EXPERT,
            domain_id="chemical",
            draft=_draft(citation="C999"),
            evidence=_evidence("C1"),
        )
        emitted: list[tuple[str, dict]] = []

        async def emit(kind, data):
            emitted.append((kind, data))

        outcome = asyncio.run(
            orchestrator._pipeline(state, registry.get("chemical"), _expert(), emit, start="output")
        )
        assert outcome.interrupted is True
        assert state.interrupt_kind == "output_review"
        assert state.interrupt_payload["review_id"]
        assert any(name == "clarify" and data.get("kind") == "output_review" for name, data in emitted)
        assert not any(name == "token" for name, _ in emitted)
        messages = seeded.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == "S-review")
        ).scalar()
        assert messages == 0, "审核前不得落库/发送正文"
        review = seeded.get(OutputReview, state.interrupt_payload["review_id"])
        assert review.status == "pending"
        assert review.draft_digest == draft_digest(state.draft)
        seeded.expire_all()
        from core.runs.models import AgentRun

        assert seeded.get(AgentRun, run.run_id).status == "waiting_review"

    def test_should_send_after_approval_bound_to_draft_digest(self, seeded, registry):
        run = RunService(seeded).create_run(
            session_id="S-review-2", owner_id="u-expert", domain_id="chemical"
        )
        orchestrator = Orchestrator(seeded, registry=registry)
        draft = _draft(citation="C999")
        review = OutputReviewService(seeded).create(
            run_id=run.run_id,
            session_id="S-review-2",
            owner_id="u-expert",
            domain_id="chemical",
            draft=draft,
            action="review",
            reasons=["存在无效引用"],
            policy_version="output-policy-v1",
        )
        OutputReviewService(seeded).decide(review.review_id, "approve", _admin())

        modified = _draft()
        modified.summary = "被改动"
        with pytest.raises(OutputReviewConflict):
            OutputReviewService(seeded).consume(review.review_id, draft_digest(modified), _expert())
        consumed = OutputReviewService(seeded).consume(review.review_id, draft_digest(draft), _expert())
        assert consumed.status == "consumed"

        state = TurnState(
            session_id="S-review-2",
            turn_id="T-review-2",
            run_id=run.run_id,
            user_id="u-expert",
            role_id=Roles.EXPERT,
            domain_id="chemical",
            draft=draft,
            evidence=_evidence("C1"),
            plan={"output_review_approval": review.review_id},
        )
        emitted: list[tuple[str, dict]] = []

        async def emit(kind, data):
            emitted.append((kind, data))

        asyncio.run(orchestrator._node_output(state, registry.get("chemical"), _expert(), emit))
        assert any(name == "token" for name, _ in emitted)
        assert state.assistant_message_id
        assert any("已通过人工审核" in note for note in state.draft.annotations)

    def test_should_reject_output_review_and_block_consumption(self, seeded):
        review = OutputReviewService(seeded).create(
            run_id="RUN-reject",
            session_id="S-reject",
            owner_id="u-expert",
            domain_id="chemical",
            draft=_draft(confidence=0.0),
            action="review",
            reasons=["测试拒绝"],
            policy_version="output-policy-v1",
        )
        OutputReviewService(seeded).decide(review.review_id, "reject", _admin())
        with pytest.raises(OutputReviewConflict):
            OutputReviewService(seeded).consume(review.review_id, draft_digest(_draft(confidence=0.0)), _expert())

    def test_should_decide_via_api_and_reject_repeat(self, client, seeded, admin_headers):
        review = OutputReviewService(seeded).create(
            run_id="RUN-api-review",
            session_id="S-api-review",
            owner_id="u-expert",
            domain_id="chemical",
            draft=_draft(confidence=0.0),
            action="review",
            reasons=["测试"],
            policy_version="output-policy-v1",
        )
        pending = client.get("/api/v1/output-reviews?domain_id=chemical", headers=admin_headers)
        assert pending.status_code == 200
        assert any(item["review_id"] == review.review_id for item in pending.json()["data"])
        first = client.post(
            f"/api/v1/output-reviews/{review.review_id}/decide",
            json={"action": "approve"},
            headers=admin_headers,
        )
        assert first.status_code == 200
        assert first.json()["data"]["status"] == "approved"
        repeat = client.post(
            f"/api/v1/output-reviews/{review.review_id}/decide",
            json={"action": "reject"},
            headers=admin_headers,
        )
        assert repeat.status_code == 409
        assert repeat.json()["code"] == "OUTPUT_REVIEW_CONFLICT"


class TestStandardPatch:
    def test_should_reject_invalid_weight_patch(self, client, seeded, admin_headers):
        resp = client.post(
            "/api/v1/growth/standards/propose",
            json={
                "domain_id": "chemical",
                "note": "调权重",
                "rationale": "测试非法权重",
                "patch": {"dimension_weights": {"D1": 30, "D2": 20}},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert "100" in resp.json()["message"]

    def test_should_reject_unknown_dimension_patch(self, client, seeded, admin_headers):
        resp = client.post(
            "/api/v1/growth/standards/propose",
            json={
                "domain_id": "chemical",
                "rationale": "测试未知维度",
                "patch": {"dimension_weights": {"D1": 80, "DX": 20}},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "未知评分维度" in resp.json()["message"]

    def test_should_publish_patched_standard_and_archive_previous(self, seeded):
        from core.evaluation.models import EvalStandard
        from core.growth.service import GrowthService

        service = GrowthService(seeded)
        result = service.propose_standard_update(
            "chemical",
            _admin(),
            note="调整权重",
            rationale="专家盲评支持",
            patch={"dimension_weights": {"D1": 32, "D2": 18, "D3": 15, "D4": 15, "D5": 12, "D6": 8}},
        )
        draft = seeded.get(EvalStandard, result["standard_id"])
        assert draft is not None and draft.status == "draft"
        service.publish_standard(result["standard_id"], "u-admin", _admin())
        seeded.expire_all()
        assert seeded.get(EvalStandard, result["standard_id"]).status == "active"
        actives = seeded.execute(
            select(EvalStandard).where(EvalStandard.domain_id == "chemical", EvalStandard.status == "active")
        ).scalars().all()
        assert len(actives) == 1
        assert actives[0].standard_id == result["standard_id"]
