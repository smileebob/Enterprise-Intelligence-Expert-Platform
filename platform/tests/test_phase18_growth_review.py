"""Phase 18 · 06-B 成长消费 + 06-C 校准一致性（G01/G02/E01 的 SQLite 切片）。"""

from __future__ import annotations

import asyncio
import json

from core.abstraction.dtos import Actor, ChatResult
from core.abstraction.enums import FeedbackRating, Roles
from core.evaluation.engine import EvaluationEngine
from core.growth.constraints import apply_constraint_rules
from core.growth.service import GrowthService
from core.knowledge.service import KnowledgeService
from core.orchestration.state import Claim, Draft, DraftSection


def _kb_admin() -> Actor:
    return Actor(
        user_id="u-kbadmin", role_id=Roles.KB_ADMIN, permission_max=2, domain_scope=["chemical"]
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


class TestConfirmedCaseRetrieval:
    def test_should_retrieve_case_only_after_review_ready(self, seeded):
        service = GrowthService(seeded)
        result = service.record_feedback(
            user_id="u-expert",
            domain_id="chemical",
            rating=FeedbackRating.ADOPTED,
            session_id="S-case",
            message_id="MSG-case",
            message_content="无氟防水案例：涤纶面料50次耐洗保持率85%，客户已验收。",
            citations=[],
        )
        case_chunk = f"chk:cases:{result['feedback_id']}"
        knowledge = KnowledgeService(seeded)
        before = asyncio.run(
            knowledge.retrieve("无氟防水 涤纶 50次 耐洗 客户验收", "chemical", permission_max=2)
        )
        assert case_chunk not in {item.chunk_id for item in before.evidence}
        assert all(item.source_type != "confirmed" for item in before.evidence)

        service.review_case(result["feedback_id"], "approve", _kb_admin())
        after = asyncio.run(
            knowledge.retrieve("无氟防水 涤纶 50次 耐洗 客户验收", "chemical", permission_max=2)
        )
        confirmed = [item for item in after.evidence if item.source_type == "confirmed"]
        assert {item.chunk_id for item in confirmed} == {case_chunk}
        assert after.partition_stats.get("confirmed") == 1

    def test_should_keep_rejected_case_out_of_retrieval(self, seeded):
        service = GrowthService(seeded)
        result = service.record_feedback(
            user_id="u-expert",
            domain_id="chemical",
            rating=FeedbackRating.ADOPTED,
            session_id="S-case-2",
            message_id="MSG-case-2",
            message_content="被驳回的案例：低黄变亲水整理方案。",
            citations=[],
        )
        service.review_case(result["feedback_id"], "reject", _kb_admin())
        fused = asyncio.run(
            KnowledgeService(seeded).retrieve("低黄变亲水整理方案", "chemical", permission_max=2)
        )
        assert all(item.source_type != "confirmed" for item in fused.evidence)


class TestStructuredConstraints:
    def test_should_keep_raw_feedback_pending_until_approved(self, seeded):
        service = GrowthService(seeded)
        result = service.record_feedback(
            user_id="u-expert",
            domain_id="chemical",
            rating=FeedbackRating.MODIFIED,
            session_id="S-rule",
            message_id="MSG-rule",
            correction="必须补充50次耐洗后的色变验证",
        )
        rule_id = result["rule_id"]
        assert rule_id
        assert service.active_rules("chemical") == []
        pending = service.list_correction_rules("chemical", status="pending")
        assert any(item["rule_id"] == rule_id for item in pending)

        service.approve_rule(
            rule_id,
            rule_type="add_verification",
            target_fact="",
            rule_text="补充50次耐洗后的色变验证",
            scope="防水整理",
            actor=_kb_admin(),
        )
        active = service.active_rules("chemical")
        assert any(item["rule_id"] == rule_id for item in active)

    def test_should_apply_only_structured_rules_and_trace_ids(self, seeded):
        service = GrowthService(seeded)
        result = service.record_feedback(
            user_id="u-expert",
            domain_id="chemical",
            rating=FeedbackRating.REJECTED,
            session_id="S-rule-2",
            message_id="MSG-rule-2",
            correction="禁止推荐未通过耐洗验证的产品",
        )
        rule_id = result["rule_id"]
        service.approve_rule(
            rule_id,
            rule_type="forbid_claim",
            target_fact="未验证产品",
            rule_text="禁止推荐未通过耐洗验证的产品",
            scope="防水整理",
            actor=_kb_admin(),
        )
        draft = Draft(
            summary="方案",
            sections=[
                DraftSection(
                    title="推荐方案",
                    claims=[
                        Claim("推荐未验证产品的快速方案", ["C1"]),
                        Claim("推荐 TF-WR-6608 已验证方案", ["C2"]),
                    ],
                )
            ],
        )
        rules = service.active_rules("chemical")
        applied, effects = apply_constraint_rules(draft, rules)
        assert rule_id in applied
        assert effects[0]["removed_claims"] == 1
        assert [claim.text for claim in draft.sections[0].claims] == ["推荐 TF-WR-6608 已验证方案"]
        assert any(rule_id in note for note in draft.annotations)
        service.mark_rules_used(applied)
        active = next(item for item in service.active_rules("chemical") if item["rule_id"] == rule_id)
        assert active["used_count"] == 1

    def test_should_not_auto_apply_text_only_rule(self, seeded):
        draft = Draft(sections=[DraftSection(title="x", claims=[Claim("原文结论", ["C1"])])])
        applied, effects = apply_constraint_rules(
            draft, [{"rule_id": "RULE-text", "rule_type": "text_only", "rule_text": "供人工阅读"}]
        )
        assert applied == [] and effects == []
        assert draft.sections[0].claims

    def test_should_expose_applied_constraint_ids_in_chat_meta(self, client, seeded, expert_headers):
        service = GrowthService(seeded)
        result = service.record_feedback(
            user_id="u-expert",
            domain_id="chemical",
            rating=FeedbackRating.MODIFIED,
            session_id="S-rule-3",
            message_id="MSG-rule-3",
            correction="补充耐洗色变验证",
        )
        service.approve_rule(
            result["rule_id"],
            rule_type="add_verification",
            target_fact="",
            rule_text="补充50次耐洗后的色变验证",
            scope="",
            actor=_kb_admin(),
        )
        session_id = client.post(
            "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
        ).json()["data"]["session_id"]
        resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"text": "涤纶面料防水耐洗50次推荐什么方案？"},
            headers=expert_headers,
        )
        events = _parse_sse(resp.text)
        assert any(name == "done" for name, _ in events)
        messages = client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers=expert_headers
        ).json()["data"]["items"]
        assistant = next(item for item in messages if item["role"] == "assistant")
        assert result["rule_id"] in assistant["meta"]["applied_constraint_ids"]
        assert any("补充验证要求" in note for note in assistant["meta"]["annotations"])


class _CalibrationGateway:
    def __init__(self, calibrations: list[dict]):
        self.calibrations = calibrations
        self.embed_model_name = "fake"

    async def chat(self, messages, **kwargs):
        return ChatResult(
            content=json.dumps({"calibrations": self.calibrations}, ensure_ascii=False),
            model="fake",
            degrade_level="L0",
        )


class TestCalibrationConsistency:
    def _dimensions(self):
        return {
            "D1": {"score": 50.0, "evidence_refs": ["C1"]},
            "D6": {
                "score": 20.0,
                "evidence_refs": ["C1"],
                "detail": {"triggered": True, "hard_cap": 30},
            },
        }

    def test_should_apply_whole_batch_and_cap_d6_after_calibration(self, seeded):
        from core.domains import DomainRegistry

        plugin = DomainRegistry.load_default().require("chemical")
        gateway = _CalibrationGateway(
            [
                {"dim": "D1", "delta": 12, "rationale": "补充证据", "citations": ["C1"]},
                {"dim": "D6", "delta": 10, "rationale": "说明充分", "citations": ["C1"]},
            ]
        )
        engine = EvaluationEngine(seeded, gateway=gateway)
        dimensions = self._dimensions()
        result = asyncio.run(
            engine._calibrate({"conflicts": [{"status": "unresolved"}]}, plugin, dimensions, 60.0)
        )
        assert result["applied"] is True
        assert dimensions["D1"]["score"] == 60.0
        assert dimensions["D6"]["score"] == 30.0
        engine._apply_hard_caps(dimensions)
        assert dimensions["D6"]["score"] == 30.0
        weights = {"D1": 30, "D6": 8}
        total = round(sum(dimensions[code]["score"] * weights[code] / 100 for code in weights), 1)
        assert total == 20.4

    def test_should_reject_whole_batch_when_delta_exceeds_limit(self, seeded):
        from core.domains import DomainRegistry

        plugin = DomainRegistry.load_default().require("chemical")
        gateway = _CalibrationGateway(
            [{"dim": "D1", "delta": 16, "rationale": "超幅", "citations": ["C1"]}]
        )
        dimensions = self._dimensions()
        result = asyncio.run(
            EvaluationEngine(seeded, gateway=gateway)._calibrate(
                {"conflicts": [{"status": "unresolved"}]}, plugin, dimensions, 60.0
            )
        )
        assert result["applied"] is False
        assert result["needs_human_calibration"] is True
        assert dimensions["D1"]["score"] == 50.0

    def test_should_reject_batch_when_citation_not_in_available_evidence(self, seeded):
        from core.domains import DomainRegistry

        plugin = DomainRegistry.load_default().require("chemical")
        gateway = _CalibrationGateway(
            [{"dim": "D1", "delta": 5, "rationale": "引用不存在", "citations": ["C999"]}]
        )
        dimensions = self._dimensions()
        result = asyncio.run(
            EvaluationEngine(seeded, gateway=gateway)._calibrate(
                {"conflicts": [{"status": "unresolved"}]}, plugin, dimensions, 60.0
            )
        )
        assert result["applied"] is False
        assert result["rejected_items"][0]["dim"] == "D1"
        assert dimensions["D1"]["score"] == 50.0

    def test_should_compute_total_after_calibration_and_caps(self, seeded):
        request = {
            "domain_id": "chemical",
            "object_type": "solution",
            "object_ref": {
                "products": ["TF-WR-6608"],
                "formula": "FORM-6608-WR",
                "process": "PROC-POLY-WR",
                "target_market": "内销",
            },
            "scenario": "默认",
        }
        result = asyncio.run(EvaluationEngine(seeded).evaluate(request, persist=False))
        assert result["baseline_total"] == result["total_score"]
        assert all(dim["score"] <= 100 for dim in result["dimensions"].values())
