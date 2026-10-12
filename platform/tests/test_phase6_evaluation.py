"""Phase 6 验证：六维打分器金值对拍（83.8/69.3/84.6/83.1，误差 ≤0.1）、CS-05 反转、
护栏双层、校准协议（±10 限幅 / >15 转人工 / Schema 拒收）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from adapters.model_gateway import OfflineModelGateway
from core.abstraction.dtos import Actor, ChatResult
from core.abstraction.enums import Roles
from core.domains import DomainRegistry
from core.evaluation.engine import EvaluationEngine
from domains.chemical.evaluation import scorers

DEMO_CASES = Path("data/mock/eval_demo_cases.json")


@pytest.fixture()
def registry():
    return DomainRegistry.load_default()


def _inputs(demo_key: str) -> dict:
    data = json.loads(DEMO_CASES.read_text(encoding="utf-8"))
    case = next(c for c in data["cases"] if c["key"] == demo_key)
    return case["inputs"]


def _score_all(inputs: dict) -> dict[str, float]:
    return {
        "D1": scorers.d1_performance(inputs)["score"],
        "D2": scorers.d2_process(inputs)["score"],
        "D3": scorers.d3_fabric(inputs)["score"],
        "D4": scorers.d4_validation(inputs)["score"],
        "D5": scorers.d5_cost(inputs)["score"],
        "D6": scorers.d6_compliance(inputs)["score"],
    }


WEIGHTS_EU = {"D1": 25, "D2": 15, "D3": 15, "D4": 15, "D5": 10, "D6": 20}
WEIGHTS_PREMIUM = {"D1": 40, "D2": 15, "D3": 15, "D4": 15, "D5": 7, "D6": 8}
WEIGHTS_DEFAULT = {"D1": 30, "D2": 20, "D3": 15, "D4": 15, "D5": 12, "D6": 8}


def _total(scores: dict, weights: dict) -> float:
    return round(sum(scores[code] * weights[code] / 100 for code in scores), 1)


class TestGoldenScorers:
    """与《附-六维评分参考实现.py》逐维比对（机器对拍，误差 ≤0.1）。"""

    def test_case1_should_match_83_8(self):
        scores = _score_all(_inputs("demo-6608-eu"))
        assert scores["D1"] == pytest.approx(85.6, abs=0.1)
        assert scores["D2"] == pytest.approx(75, abs=0.1)
        assert scores["D3"] == pytest.approx(82.5, abs=0.1)
        assert scores["D4"] == pytest.approx(65, abs=0.1)
        assert scores["D5"] == pytest.approx(90, abs=0.1)
        assert scores["D6"] == pytest.approx(100, abs=0.1)
        assert _total(scores, WEIGHTS_EU) == pytest.approx(83.8, abs=0.1)

    def test_case2_should_match_69_3_with_cs05_reversal(self):
        scores = _score_all(_inputs("demo-6612-eu"))
        assert scores["D1"] == pytest.approx(100, abs=0.1)
        assert scores["D6"] == pytest.approx(30, abs=0.1)  # CS-05：min(70,30)
        assert _total(scores, WEIGHTS_EU) == pytest.approx(69.3, abs=0.1)
        flags = scorers.d6_compliance(_inputs("demo-6612-eu"))["compliance_flags"]
        assert flags and flags[0]["rule_id"] == "CS-05-PFAS-EU"

    def test_case3_should_match_84_6_and_83_1_by_scenario(self):
        scores = _score_all(_inputs("demo-2210-premium"))
        assert _total(scores, WEIGHTS_PREMIUM) == pytest.approx(84.6, abs=0.1)
        assert _total(scores, WEIGHTS_DEFAULT) == pytest.approx(83.1, abs=0.1)

    def test_d4_should_return_25_with_forced_note_when_no_cases(self):
        result = scorers.d4_validation({"n_cases": 0, "cases": []})
        assert result["score"] == 25.0
        assert result["detail"]["n_lt2"] is True

    def test_d6_should_cap_when_market_restricted(self):
        result = scorers.d6_compliance(
            {"products": [{"product_id": "X", "compliance_level": "restricted_substance", "market_restricted": True}], "market": "eu"}
        )
        assert result["score"] == 30
        # 内销不触发（CS-05 负向）
        result_domestic = scorers.d6_compliance(
            {"products": [{"product_id": "X", "compliance_level": "restricted_substance", "market_restricted": False}], "market": "domestic"}
        )
        assert result_domestic["score"] == 70


class TestEvaluationEngine:
    def _engine(self, db_session, registry, gateway=None):
        return EvaluationEngine(db_session, gateway=gateway or OfflineModelGateway(), registry=registry)

    def test_should_evaluate_demo_case_to_golden_total(self, db_session, registry):
        engine = self._engine(db_session, registry)
        result = asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
                    "scenario": "出口合规敏感",
                },
                actor=Actor(user_id="u1", role_id=Roles.EXPERT, permission_max=2),
            )
        )
        assert result["total_score"] == pytest.approx(83.8, abs=0.1)
        assert result["dimensions"]["D6"]["score"] == pytest.approx(100, abs=0.1)
        assert result["standard_version"]
        assert result["guardrails"], "应输出结论级护栏标注"
        assert any("参考级" in g for g in result["guardrails"])

    def test_should_flag_compliance_and_guardrails_for_cs05_case(self, db_session, registry):
        engine = self._engine(db_session, registry)
        result = asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": {"demo_case": "demo-6612-eu", "products": ["TF-WR-6612"]},
                    "scenario": "出口合规敏感",
                },
                actor=None,
            )
        )
        assert result["total_score"] == pytest.approx(69.3, abs=0.1)
        assert result["compliance_flags"] and result["compliance_flags"][0]["rule_id"] == "CS-05-PFAS-EU"
        assert result["dimensions"]["D6"]["score"] <= 30

    def test_should_be_idempotent_by_key(self, db_session, registry):
        engine = self._engine(db_session, registry)
        request = {
            "domain_id": "chemical",
            "object_type": "solution",
            "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
            "scenario": "出口合规敏感",
        }
        first = asyncio.run(engine.evaluate(request, actor=None, idempotency_key="idem-golden"))
        second = asyncio.run(engine.evaluate(request, actor=None, idempotency_key="idem-golden"))
        assert first["eval_id"] == second["eval_id"]

    def test_should_confirm_and_record_correction(self, db_session, registry):
        engine = self._engine(db_session, registry)
        result = asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
                    "scenario": "出口合规敏感",
                }
            )
        )
        updated = engine.confirm(
            result["eval_id"],
            {"action": "modify", "corrections": {"D1": 80}, "reason": "按客户实测调整"},
            Actor(user_id="expert1", role_id=Roles.EXPERT),
        )
        assert updated["confirmed_by"] == "expert1"
        from core.growth.models import GrowthFeedback

        rows = db_session.query(GrowthFeedback).all()
        assert rows and rows[0].rating == "modified"


class FakeGateway:
    """可控 chat 输出的假网关（校准协议测试用）。"""

    def __init__(self, payload: str):
        self.payload = payload

    async def chat(self, messages, *, model_alias="default", temperature=0.0, json_mode=False, timeout_s=30):
        return ChatResult(content=self.payload, model="fake", degrade_level="L0")

    async def embed(self, texts, *, model_alias="embed"):
        offline = OfflineModelGateway()
        return [offline._hash_embed(text) for text in texts]


class TestCalibrationProtocol:
    def _run(self, db_session, registry, payload: str):
        engine = EvaluationEngine(db_session, gateway=FakeGateway(payload), registry=registry)
        return asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
                    "scenario": "出口合规敏感",
                    "force_calibrate": True,
                }
            )
        )

    def test_should_apply_clamped_delta(self, db_session, registry):
        payload = json.dumps(
            {"calibrations": [{"dim": "D1", "delta": 30, "rationale": "边界案例", "citations": ["EXP-2201"]}]}
        )
        result = self._run(db_session, registry, payload)
        # 原始 30 > 15 → 挂人工、不应用
        assert result["calibration"]["needs_human_calibration"] is True
        assert result["calibration"]["applied"] is False
        assert any("人工" in g for g in result["guardrails"])

    def test_should_clamp_delta_within_10(self, db_session, registry):
        payload = json.dumps(
            {"calibrations": [{"dim": "D1", "delta": 12, "rationale": "轻微上调", "citations": ["EXP-2201"]}]}
        )
        result = self._run(db_session, registry, payload)
        assert result["calibration"]["applied"] is True
        assert result["dimensions"]["D1"]["calibrated_delta"] == 10  # 强制限幅

    def test_should_reject_invalid_json(self, db_session, registry):
        result = self._run(db_session, registry, "not-a-json")
        assert result["calibration"]["applied"] is False
        assert "Schema" in result["calibration"]["reason"] or "不合规" in result["calibration"]["reason"]

    def test_should_reject_missing_citations(self, db_session, registry):
        payload = json.dumps({"calibrations": [{"dim": "D1", "delta": 3, "rationale": "无引用", "citations": []}]})
        result = self._run(db_session, registry, payload)
        assert result["calibration"]["applied"] is False


class TestBR05Bins:
    def test_should_label_bins(self, registry, db_session):
        engine = EvaluationEngine(db_session, registry=registry)
        cases = [
            (0, "参考级"),
            (4, "参考级"),
            (5, "低置信"),
            (10, "中置信"),
            (30, "高置信"),
        ]
        for n, expected in cases:
            guardrails = engine._guardrails({"n_cases": n}, [], {})
            assert any(expected in g for g in guardrails), f"n={n} 应标注 {expected}"
