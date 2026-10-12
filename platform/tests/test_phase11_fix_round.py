"""Phase 11 验收修复轮测试（F-02/F-03/F-04/F-05/F-06/F-07/F-08/F-10 关联）。

设计文档：tests/test-design-fix-round.md（A~I 共 44 用例）；快修包追加 J 组与 A6/B8（共 48 用例）。
分组：
- A 网关按请求超时透传（respx，不触真实网络；A6 持续 5xx → LlmUnavailableError）
- B 降级层级单调上浮（白盒 + 集成，offline / Resilient+熔断；B8 预置 L2 不回落）
- C 冲突 meta 按角色脱敏（expert 掩码 / kbadmin 放行 / 不过度掩码）
- D seed_standards 内容一致性与幂等
- E 标准版本状态机（propose -v2 / publish 归档 / 审计 / BR-09 / 权重复制）
- F 入库白名单前置校验（400 先于建任务）
- G 冲突处置措辞（合规例外 vs 以内部为准 / resolved 边界 / 匹配口径）
- H 自定义指标（12 族名 + 增量样本，进程级 REGISTRY 只做差值断言）
- I 回归脚本 trap 判定纯逻辑真值表（脚本闭包免测）
- J 快修包（D-22 认证异常收窄留痕；A-09 密码哈希下沉后的导入兼容）

约束：不修改业务代码；测试数据 fixture 内构造；无裸 sleep；进程级全局指标只做前后快照差值。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pybreaker
import pytest
import respx
from fastapi.testclient import TestClient

import core.metrics  # noqa: F401 - 显式注册 12 个自定义指标族（/metrics 序列化依赖）
from adapters.model_gateway import OfflineModelGateway, OpenAiCompatGateway, ResilientGateway
from app import create_app
from app.config import get_settings
from app.db import get_db
from core.abstraction.dtos import (
    Actor,
    ConflictClaims,
    ConflictRecord,
    EvalStandardRecord,
    Evidence,
)
from core.abstraction.enums import Roles
from core.abstraction.errors import LlmUnavailableError
from core.admin.seed import seed_all, seed_standards
from core.domains import DomainRegistry
from core.evaluation.engine import EvaluationEngine
from core.growth.service import GrowthService
from core.guardrails.masking import mask_text_for_role
from core.guardrails.output_hook import _render_conflicts
from core.knowledge.models import KbIngestTask
from core.orchestration.engine import Orchestrator
from core.orchestration.session import ChatSessionStore
from core.orchestration.state import Draft, Need, TurnState
from core.runtime import eval_repo
from tests.conftest import MOCK_DIR

# ---------------- 公共小工具 ----------------


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """SSE 文本 → [(event, data)]（test_phase9 同款写法，避免跨文件 import 测试模块）。"""
    events = []
    for block in text.strip().split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name and data is not None:
            events.append((name, data))
    return events


def _new_session(db, user_id="u-expert", title="phase11"):
    store = ChatSessionStore(db)
    return store.create_session("chemical", user_id, title=title)


def _run_turn(orchestrator, session, text, actor, message_id="MSG-P11"):
    return asyncio.run(orchestrator.run_turn(session, text, actor, message_id=message_id))


def _assistant_message(db, session_id):
    store = ChatSessionStore(db)
    messages, _ = store.list_messages(session_id)
    return next(m for m in messages if m.role == "assistant")


async def _noop_emit(kind, data):
    return None


async def _faulty_chat(messages, *, model_alias="default", temperature=0.0, json_mode=False, timeout_s=None):
    raise LlmUnavailableError("primary channel down")


def _metrics_samples(text: str) -> list[tuple[str, dict[str, str], float]]:
    """解析 prometheus 文本 → [(metric_name, labels, value)]（容忍标签字母序）。"""
    samples: list[tuple[str, dict[str, str], float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "{" in line and "}" in line:
            name, rest = line.split("{", 1)
            labels_raw, value = rest.rsplit("}", 1)
            labels: dict[str, str] = {}
            for pair in labels_raw.split(","):
                key, val = pair.split("=", 1)
                labels[key.strip()] = val.strip().strip('"')
            samples.append((name.strip(), labels, float(value.strip())))
        else:
            name, value = line.rsplit(" ", 1)
            samples.append((name.strip(), {}, float(value.strip())))
    return samples


def _metric_sum(text: str, family: str, **labels: str) -> float:
    total = 0.0
    for name, lbl, value in _metrics_samples(text):
        if name == family and all(lbl.get(k) == v for k, v in labels.items()):
            total += value
    return total


def _metric_exists(text: str, family: str, **labels: str) -> bool:
    return any(
        name == family and all(lbl.get(k) == v for k, v in labels.items())
        for name, lbl, _ in _metrics_samples(text)
    )


def _metrics_text() -> str:
    from prometheus_client import generate_latest

    return generate_latest().decode("utf-8")


# ---------------- 局部 fixture（conftest 不提供，phase5/10 同款） ----------------


@pytest.fixture(scope="module")
def scenarios() -> dict[str, dict]:
    data = json.loads((MOCK_DIR / "conflict_scenarios.json").read_text(encoding="utf-8"))
    return {s["conflict_id"]: s for s in data["conflict_scenarios"]}


@pytest.fixture()
def registry():
    return DomainRegistry.load_default()


@pytest.fixture()
def orchestrator(db_session, registry):
    return Orchestrator(db_session, gateway=OfflineModelGateway(), registry=registry)


@pytest.fixture()
def make_orchestrator(db_session, registry):
    def _make(gateway=None):
        return Orchestrator(db_session, gateway=gateway or OfflineModelGateway(), registry=registry)

    return _make


@pytest.fixture()
def actor_expert():
    return Actor(user_id="u-expert", username="expert", role_id=Roles.EXPERT, permission_max=2)


@pytest.fixture()
def actor_kb():
    return Actor(user_id="u-kbadmin", username="kbadmin", role_id=Roles.KB_ADMIN, permission_max=2)


@pytest.fixture()
def metrics_enabled_settings(monkeypatch):
    """TestingConfig 硬置 METRICS_ENABLED=False（环境变量/类属性无效）——对缓存实例临时开闸。"""
    settings = get_settings("testing")
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    return settings


# ================================================================
# A 组：网关按请求超时（F-02，respx，不触真实网络）
# ================================================================
class TestGatewayTimeoutPassthrough:
    OK_BODY = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "model": "qwen-test",
    }
    MESSAGES = [{"role": "user", "content": "hi"}]

    @pytest.fixture()
    def gateway(self):
        gw = OpenAiCompatGateway(
            base_url="http://llm.test",
            api_key="test-key",
            model="qwen-test",
            embed_model="emb-test",
            timeout_s=30.0,
        )
        yield gw
        asyncio.run(gw.client.aclose())

    def test_should_send_per_request_timeout_when_timeout_s_passed(self, gateway):
        # given：respx 拦截 /chat/completions
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions").mock(
                return_value=httpx.Response(200, json=self.OK_BODY)
            )
            # when：显式传 timeout_s=7.5
            result = asyncio.run(gateway.chat(self.MESSAGES, timeout_s=7.5))
        # then：逐请求超时写入 request.extensions（httpx build_request 的 4 值字典）
        assert route.call_count == 1
        assert route.calls[0].request.extensions["timeout"] == {
            "connect": 7.5,
            "read": 7.5,
            "write": 7.5,
            "pool": 7.5,
        }
        assert result.degrade_level == "L0"

    def test_should_keep_client_default_timeout_when_timeout_s_none(self, gateway):
        # given / when：不传 timeout_s（None → kwargs 为空，用客户端默认）
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions").mock(
                return_value=httpx.Response(200, json=self.OK_BODY)
            )
            asyncio.run(gateway.chat(self.MESSAGES))
        # then：客户端默认 Timeout(connect=5, read=30, write=10, pool=5)
        assert route.calls[0].request.extensions["timeout"] == {
            "connect": 5.0,
            "read": 30.0,
            "write": 10.0,
            "pool": 5.0,
        }

    def test_should_parse_response_and_json_mode_payload(self, gateway):
        # given / when：json_mode 应附加 response_format
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions").mock(
                return_value=httpx.Response(200, json=self.OK_BODY)
            )
            result = asyncio.run(gateway.chat(self.MESSAGES, json_mode=True))
        # then：解析字段与请求载荷
        assert result.content == "ok"
        assert result.model == "qwen-test"
        assert result.tokens_in == 3 and result.tokens_out == 2
        payload = json.loads(route.calls[0].request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["model"] == "qwen-test"

    def test_should_retry_with_timeout_kept_when_first_call_times_out(self, gateway):
        # given：首调 ConnectTimeout，次调成功（tenacity 2 次尝试）
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions")
            route.mock(
                side_effect=[httpx.ConnectTimeout("timeout"), httpx.Response(200, json=self.OK_BODY)]
            )
            # when
            result = asyncio.run(gateway.chat(self.MESSAGES, timeout_s=7.5))
        # then：重试且两次都续带逐请求超时
        assert route.call_count == 2
        for call in route.calls:
            assert call.request.extensions["timeout"]["read"] == 7.5
        assert result.content == "ok"

    def test_should_fail_fast_when_upstream_401(self, gateway):
        # given：上游 401（业务错误不重试）
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions").mock(
                return_value=httpx.Response(401, json={"error": "invalid key"})
            )
            # when / then
            with pytest.raises(LlmUnavailableError):
                asyncio.run(gateway.chat(self.MESSAGES))
        assert route.call_count == 1

    def test_should_map_repeated_5xx_to_llm_unavailable(self, gateway):
        # 快修包①：持续 5xx 经重试后（RemoteProtocolError）不再逃逸降级链
        with respx.mock:
            route = respx.post("http://llm.test/chat/completions").mock(
                return_value=httpx.Response(500, json={"error": "upstream boom"})
            )
            # when / then：统一转 LlmUnavailableError，供上层按 L1/L2 降级
            with pytest.raises(LlmUnavailableError):
                asyncio.run(gateway.chat(self.MESSAGES))
        assert route.call_count == 2  # tenacity 2 次尝试后放弃


# ================================================================
# B 组：降级层级（F-03）
# ================================================================
class _FaultyPrimary(OfflineModelGateway):
    """离线主通道 + 打开熔断器：chat 恒失败（embed 继承离线确定性通道）。"""

    def __init__(self):
        super().__init__()
        self.breaker = pybreaker.CircuitBreaker(fail_max=1, reset_timeout=60)
        self.breaker.open()

    async def chat(self, messages, *, model_alias="default", temperature=0.0, json_mode=False, timeout_s=None):
        raise LlmUnavailableError("primary channel down")


def _eval_state(actor: Actor) -> TurnState:
    """B4-B7 白盒状态：evidence 带 products 元数据使 _assemble_object_ref 产出 object_ref。"""
    return TurnState(
        session_id="S-unit-eval",
        turn_id="T-unit-eval",
        user_id=actor.user_id,
        role_id=actor.role_id,
        domain_id="chemical",
        need=Need(raw_text="防水方案", target_performance=["防水"], market="eu", scenario="出口合规敏感"),
        plan={"evaluate": True},
        evidence=[
            Evidence(
                chunk_id="chk:products:TF-WR-6608",
                content="产品[TF-WR-6608] 类别 防水剂",
                source_type="internal",
                confidence=0.9,
                citation_id="C1",
                meta={"record_type": "products", "record_id": "TF-WR-6608", "fields": {"category": "防水剂"}},
            )
        ],
    )


_FAKE_EVAL_L2 = {
    "eval_id": "EV-x",
    "total_score": 80.0,
    "confidence": 0.8,
    "degrade_level": "L2",
    "dimensions": {},
    "compliance_flags": [],
}


class TestDegradeMonotonic:
    """B1-B3：_raise_degrade 只升不降、未知层级不抬升。"""

    def test_should_raise_level_when_higher_level_given(self, orchestrator):
        state = TurnState(degrade_level="L0")
        orchestrator._raise_degrade(state, "L2")
        assert state.degrade_level == "L2"

    def test_should_keep_level_when_lower_level_given(self, orchestrator):
        state = TurnState(degrade_level="L2")
        orchestrator._raise_degrade(state, "L1")
        orchestrator._raise_degrade(state, "L0")
        assert state.degrade_level == "L2"

    def test_should_not_uplift_when_level_unknown_or_empty(self, orchestrator):
        state = TurnState(degrade_level="L0")
        orchestrator._raise_degrade(state, "LX")
        orchestrator._raise_degrade(state, "")
        assert state.degrade_level == "L0"


class TestEvaluateDegradeUplift:
    """B4-B7：_node_evaluate 后的降级层级（Resilient 上浮 / offline 不上浮 / 异常回落）。"""

    def test_should_uplift_to_eval_level_when_gateway_resilient(self, make_orchestrator, registry, actor_expert):
        # given：Resilient 网关 + 评估返回 L2
        orchestrator = make_orchestrator(ResilientGateway(OfflineModelGateway(), OfflineModelGateway()))
        state = _eval_state(actor_expert)
        # when
        with patch("core.evaluation.engine.EvaluationEngine.evaluate", new=AsyncMock(return_value=dict(_FAKE_EVAL_L2))):
            asyncio.run(orchestrator._node_evaluate(state, registry.get("chemical"), actor_expert, _noop_emit))
        # then
        assert state.eval_result is not None
        assert state.plan["object_ref"]["products"] == ["TF-WR-6608"]
        assert state.degrade_level == "L2"

    def test_should_not_uplift_when_gateway_offline(self, make_orchestrator, registry, actor_expert):
        # given：offline 确定性通道（评估返回 L1，但按口径不上浮）
        orchestrator = make_orchestrator(OfflineModelGateway())
        state = _eval_state(actor_expert)
        fake = {**_FAKE_EVAL_L2, "degrade_level": "L1"}
        # when
        with patch("core.evaluation.engine.EvaluationEngine.evaluate", new=AsyncMock(return_value=fake)):
            asyncio.run(orchestrator._node_evaluate(state, registry.get("chemical"), actor_expert, _noop_emit))
        # then
        assert state.eval_result is not None
        assert state.degrade_level == "L0"

    def test_should_keep_preseeded_level_when_eval_level_lower(self, make_orchestrator, registry, actor_expert):
        # given：回合已处于 L2，评估只报 L1 → 单调合并保持 L2
        orchestrator = make_orchestrator(ResilientGateway(OfflineModelGateway(), OfflineModelGateway()))
        state = _eval_state(actor_expert)
        state.degrade_level = "L2"
        fake = {**_FAKE_EVAL_L2, "degrade_level": "L1"}
        # when
        with patch("core.evaluation.engine.EvaluationEngine.evaluate", new=AsyncMock(return_value=fake)):
            asyncio.run(orchestrator._node_evaluate(state, registry.get("chemical"), actor_expert, _noop_emit))
        # then
        assert state.degrade_level == "L2"

    def test_should_fallback_to_l1_when_evaluate_raises(self, make_orchestrator, registry, actor_expert):
        # given：评估引擎异常（快修包②后经 _raise_degrade 单调上浮 L1）
        orchestrator = make_orchestrator(OfflineModelGateway())
        state = _eval_state(actor_expert)
        # when：异常被吞掉，不外抛
        with patch(
            "core.evaluation.engine.EvaluationEngine.evaluate",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            asyncio.run(orchestrator._node_evaluate(state, registry.get("chemical"), actor_expert, _noop_emit))
        # then
        assert state.eval_result is None
        assert state.degrade_level == "L1"

    def test_should_keep_preseeded_l2_when_evaluate_raises(self, make_orchestrator, registry, actor_expert):
        # 快修包②：评估异常分支改单调上浮——回合已处 L2 时不回落 L1
        orchestrator = make_orchestrator(OfflineModelGateway())
        state = _eval_state(actor_expert)
        state.degrade_level = "L2"
        with patch(
            "core.evaluation.engine.EvaluationEngine.evaluate",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            asyncio.run(orchestrator._node_evaluate(state, registry.get("chemical"), actor_expert, _noop_emit))
        assert state.degrade_level == "L2"


class TestDegradeIntegration:
    """B8-B9：全轮集成下的用户可见降级层级。"""

    def test_should_report_l0_when_offline_cs05_round(self, seeded, orchestrator, db_session, actor_expert, scenarios):
        # given：offline 全轮，CS-05（校准通道经 D6 触发，离线回落 L1）
        session = _new_session(db_session, actor_expert.user_id)
        # when
        outcome = _run_turn(orchestrator, session, scenarios["CS-05"]["demo_query"], actor_expert)
        # then：评估内部 L1，但不占故障语义 → 用户可见 L0
        assert outcome.state.eval_result is not None
        assert outcome.state.eval_result["degrade_level"] == "L1"
        done = next(e["data"] for e in outcome.events if e["event"] == "done")
        assert done["data"]["degrade_level"] == "L0"
        message = _assistant_message(db_session, session.session_id)
        assert message.degrade_level == "L0"

    def test_should_report_l2_when_resilient_breaker_open(self, seeded, make_orchestrator, db_session, actor_expert, scenarios):
        # given：主通道熔断打开 → Resilient 判定 L2
        orchestrator = make_orchestrator(ResilientGateway(_FaultyPrimary(), OfflineModelGateway()))
        session = _new_session(db_session, actor_expert.user_id)
        # when
        outcome = _run_turn(orchestrator, session, scenarios["CS-05"]["demo_query"], actor_expert)
        # then：评估校准经熔断回落 L2 → 回合上浮 L2
        done = next(e["data"] for e in outcome.events if e["event"] == "done")
        assert done["data"]["degrade_level"] == "L2"
        message = _assistant_message(db_session, session.session_id)
        assert message.degrade_level == "L2"


# ================================================================
# C 组：冲突元数据按角色脱敏（F-05）
# ================================================================
class TestConflictMetaMasking:
    @pytest.fixture()
    def cs01_conflicts(self, seeded, orchestrator, db_session, actor_expert, scenarios):
        """C1 前置：expert 跑 CS-01 轮并回读落库 conflicts。"""
        session = _new_session(db_session, actor_expert.user_id)
        outcome = _run_turn(orchestrator, session, scenarios["CS-01"]["demo_query"], actor_expert)
        sensitive = {f for e in outcome.state.evidence for f in (e.meta or {}).get("sensitive_fields", [])}
        assert "配方配比" in sensitive, "fixture 前提缺失：CS-01 轮证据未携带敏感字段『配方配比』"
        message = _assistant_message(db_session, session.session_id)
        conflicts = message.conflicts or []
        cs01 = next(c for c in conflicts if c["conflict_id"] == "CS-01")
        return cs01, conflicts

    def test_should_mask_conflict_meta_for_expert(self, cs01_conflicts):
        # given / when：expert 掩码（配方配比 mask）
        cs01, conflicts = cs01_conflicts
        # then：CS-01 记录含掩码、整体含冲突 json 不含原文 8g/L
        assert "•••" in cs01["internal"]
        assert "8g/L" not in json.dumps(conflicts, ensure_ascii=False)

    def test_should_allow_conflict_meta_for_kbadmin(self, seeded, orchestrator, db_session, actor_kb, scenarios):
        # given：kbadmin（配方配比 allow）
        session = _new_session(db_session, actor_kb.user_id)
        # when
        _run_turn(orchestrator, session, scenarios["CS-01"]["demo_query"], actor_kb)
        # then：反向放行，原文保留
        message = _assistant_message(db_session, session.session_id)
        cs01 = next(c for c in message.conflicts if c["conflict_id"] == "CS-01")
        assert "8g/L" in cs01["internal"]
        assert "•••" not in cs01["internal"]

    def test_should_not_over_mask_non_gpl_numbers(self, seeded, orchestrator, db_session, actor_expert, scenarios):
        # given：CS-04 轮（内部结论含 9.5 分，非 g/L 数值）
        session = _new_session(db_session, actor_expert.user_id)
        # when
        _run_turn(orchestrator, session, scenarios["CS-04"]["demo_query"], actor_expert)
        # then：9.5 不被『配方配比』正则误伤
        message = _assistant_message(db_session, session.session_id)
        cs04 = next(c for c in message.conflicts if c["conflict_id"] == "CS-04")
        assert "9.5" in cs04["internal"]
        assert "•••" not in cs04["internal"]

    def test_should_not_mask_when_no_sensitive_fields_given(self):
        # given / when：敏感字段集合为空（纯函数边界）
        text, masked = mask_text_for_role("交联剂8g/L", Roles.EXPERT, [])
        # then
        assert text == "交联剂8g/L"
        assert masked == []


# ================================================================
# D 组：seed_standards（F-04a）
# ================================================================
class TestSeedStandards:
    def _manual_standard(self, standard_id: str, status: str) -> EvalStandardRecord:
        return EvalStandardRecord(
            standard_id=standard_id,
            domain_id="chemical",
            year=2026,
            object_type="solution",
            sub_domain=None,
            standard_desc="手工构造",
            dimension_weights={},
            scoring_rubric={},
            scenario_profiles={},
            sample_size=0,
            version=standard_id,
            status=status,
            reviewed_by="tester",
            meta={},
        )

    def test_should_seed_standards_consistent_with_domain_yaml(self, db_session, registry):
        # given：fresh 库
        # when
        count = seed_standards(db_session)
        # then：两个领域各一条 active 标准，内容与插件 domain.yaml eval_config 一致
        assert count == 2
        repo = eval_repo(db_session)
        chemical = repo.get_standard("std-2026Q4-chemical-v1")
        plugin = registry.get("chemical")
        assert chemical is not None
        assert chemical.status == "active"
        assert chemical.year == 2026
        assert chemical.object_type == "solution"
        assert chemical.dimension_weights == {"D1": 30, "D2": 20, "D3": 15, "D4": 15, "D5": 12, "D6": 8}
        assert chemical.scenario_profiles == plugin.scenario_profiles
        assert len(chemical.scenario_profiles) == 5
        assert chemical.version == chemical.standard_id
        assert chemical.reviewed_by == "seed"
        assert chemical.meta["seeded_from"] == "domain.yaml eval_config"
        investment = repo.get_standard("std-2026Q4-investment-v1")
        assert investment is not None
        assert investment.object_type == "enterprise_profile"
        assert investment.dimension_weights == {"E1": 20, "E2": 20, "E3": 15, "E4": 15, "E5": 15, "E6": 15}

    def test_should_be_idempotent_when_run_twice(self, db_session):
        # given / when：连续两次播种
        first = seed_standards(db_session)
        second = seed_standards(db_session)
        # then
        assert first == 2
        assert second == 0
        assert len(eval_repo(db_session).list_standards()) == 2

    def test_should_skip_chemical_when_other_active_exists(self, db_session):
        # given：chemical 已有他 id 的 active 标准
        repo = eval_repo(db_session)
        repo.upsert_standard(self._manual_standard("std-custom-1", "active"))
        db_session.commit()
        # when
        count = seed_standards(db_session)
        # then：仅 investment 新建；chemical 保持自定义 active
        assert count == 1
        assert repo.get_standard("std-2026Q4-chemical-v1") is None
        assert repo.get_active_standard("chemical", "solution").standard_id == "std-custom-1"

    def test_should_not_revive_archived_same_ref(self, db_session):
        # given：同 ref 已存在但为 archived
        repo = eval_repo(db_session)
        repo.upsert_standard(self._manual_standard("std-2026Q4-chemical-v1", "archived"))
        db_session.commit()
        # when
        count = seed_standards(db_session)
        # then：chemical 跳过（不复活不新增）；investment 正常新建
        assert count == 1
        assert repo.get_standard("std-2026Q4-chemical-v1").status == "archived"
        assert repo.get_standard("std-2026Q4-investment-v1") is not None

    def test_should_report_standards_in_seed_all_stats(self, db_session):
        # given / when：seed_all 两次（统计口径）
        first = seed_all(db_session)
        second = seed_all(db_session)
        # then
        assert first["standards"] == 2
        assert second["standards"] == 0
        assert second == {"users": 0, "agents": 0, "domains": 0, "standards": 0}


# ================================================================
# E 组：标准版本状态机（F-04b）
# ================================================================
class TestStandardVersioning:
    def _run_eval(self, db_session, registry, object_ref):
        engine = EvaluationEngine(db_session, gateway=OfflineModelGateway(), registry=registry)
        return asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": object_ref,
                    "scenario": "出口合规敏感",
                },
                persist=True,
            )
        )

    def test_should_propose_v2_draft_when_called(self, seeded, db_session, actor_kb):
        # given：seeded（v1 active）
        # when
        result = GrowthService(db_session).propose_standard_update("chemical", actor_kb, note="验收轮")
        # then：-v2 草稿，无 Q7 漂移
        assert result["standard_id"] == "std-2026Q4-chemical-v2"
        assert result["status"] == "draft"
        assert "Q7" not in result["standard_id"]
        draft = eval_repo(db_session).get_standard("std-2026Q4-chemical-v2")
        assert draft.meta["proposed_from"] == "std-2026Q4-chemical-v1"
        assert draft.status == "draft"
        # 列表按 created_at desc：v2 在前
        ids = [s["standard_id"] for s in GrowthService(db_session).list_standards("chemical")]
        assert ids == ["std-2026Q4-chemical-v2", "std-2026Q4-chemical-v1"]

    def test_should_archive_old_and_activate_new_when_published(self, seeded, db_session, actor_kb):
        # given：先 propose
        GrowthService(db_session).propose_standard_update("chemical", actor_kb)
        # when
        result = GrowthService(db_session).publish_standard("std-2026Q4-chemical-v2", "kbadmin", actor_kb)
        # then：v1 归档、v2 active、审计留痕
        assert result == {"standard_id": "std-2026Q4-chemical-v2", "status": "active"}
        repo = eval_repo(db_session)
        assert repo.get_standard("std-2026Q4-chemical-v1").status == "archived"
        assert repo.get_standard("std-2026Q4-chemical-v2").status == "active"
        assert repo.get_active_standard("chemical", "solution").standard_id == "std-2026Q4-chemical-v2"
        from core.gateway.models import GovAuditLog

        rows = db_session.query(GovAuditLog).filter(GovAuditLog.action == "growth.standard.publish").all()
        assert rows, "发布动作应写审计"

    def test_should_compute_next_version_with_boundaries(self):
        # given / when / then：_next_version 边界
        assert GrowthService._next_version("std-2026Q4-chemical-v1") == 2
        assert GrowthService._next_version("std-2026Q4-chemical-v9") == 10
        assert GrowthService._next_version("no-suffix") == 2
        assert GrowthService._next_version("") == 2

    def test_should_raise_keyerror_when_no_current_standard(self, db_session, actor_kb):
        # given：fresh 库无任何标准
        # when / then
        with pytest.raises(KeyError) as exc_info:
            GrowthService(db_session).propose_standard_update("chemical", actor_kb)
        assert "无现行标准" in str(exc_info.value)

    def test_should_reject_publish_for_missing_or_non_draft(self, seeded, db_session, actor_kb):
        # given：seeded（v1 active）
        service = GrowthService(db_session)
        # when / then：不存在的 id → KeyError
        with pytest.raises(KeyError):
            service.publish_standard("std-2026Q4-chemical-nope", "kbadmin", actor_kb)
        # when / then：active 的 v1 不可发布 → ValueError，且状态不被改写
        with pytest.raises(ValueError) as exc_info:
            service.publish_standard("std-2026Q4-chemical-v1", "kbadmin", actor_kb)
        assert "仅 draft 状态可发布" in str(exc_info.value)
        assert eval_repo(db_session).get_standard("std-2026Q4-chemical-v1").status == "active"

    def test_should_not_recompute_history_when_publishing_v2(self, seeded, db_session, registry, actor_kb):
        # given：历史评估挂在 v1
        result = self._run_eval(db_session, registry, {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]})
        assert result["standard_id"] == "std-2026Q4-chemical-v1"
        # when：发布 v2
        service = GrowthService(db_session)
        service.propose_standard_update("chemical", actor_kb)
        service.publish_standard("std-2026Q4-chemical-v2", "kbadmin", actor_kb)
        # then：BR-09 历史评分不重算，standard_id 仍指向 v1
        stored = eval_repo(db_session).get_result(result["eval_id"])
        assert stored["standard_id"] == "std-2026Q4-chemical-v1"

    def test_should_copy_weights_so_golden_total_unchanged(self, seeded, db_session, registry, actor_kb):
        # given：v2 复制 v1 权重后发布
        service = GrowthService(db_session)
        service.propose_standard_update("chemical", actor_kb)
        service.publish_standard("std-2026Q4-chemical-v2", "kbadmin", actor_kb)
        # when：同算例重跑
        result = self._run_eval(db_session, registry, {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]})
        # then：金值不变，版本指向 v2
        assert result["total_score"] == pytest.approx(83.8, abs=0.1)
        assert result["standard_version"] == "std-2026Q4-chemical-v2"

    def test_should_walk_full_api_chain_for_standard_lifecycle(self, client, seeded, kb_headers):
        # given / when：propose → publish → 列表回溯
        proposed = client.post("/api/v1/growth/standards/propose", json={"domain_id": "chemical", "note": "P11"}, headers=kb_headers)
        assert proposed.status_code == 200, proposed.text
        standard_id = proposed.json()["data"]["standard_id"]
        assert standard_id == "std-2026Q4-chemical-v2"
        published = client.post(
            f"/api/v1/growth/standards/{standard_id}/publish", json={"reviewer": "kbadmin"}, headers=kb_headers
        )
        assert published.status_code == 200, published.text
        assert published.json()["data"]["status"] == "active"
        listed = client.get("/api/v1/evaluations/standards?domain_id=chemical", headers=kb_headers)
        assert listed.status_code == 200
        by_id = {s["standard_id"]: s for s in listed.json()["data"]}
        assert by_id["std-2026Q4-chemical-v2"]["status"] == "active"
        assert by_id["std-2026Q4-chemical-v1"]["status"] == "archived"
        # then：重复发布 v1（非 draft）→ 409 STATE_INVALID
        again = client.post(
            "/api/v1/growth/standards/std-2026Q4-chemical-v1/publish",
            json={"reviewer": "kbadmin"},
            headers=kb_headers,
        )
        assert again.status_code == 409
        assert again.json()["code"] == "STATE_INVALID"


# ================================================================
# F 组：入库白名单前置校验（F-07）
# ================================================================
class TestIngestWhitelist:
    def test_should_reject_unknown_file_before_task_creation(self, client, seeded, db_session, kb_headers):
        # given：白名单外文件名
        # when
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"domain_id": "chemical", "source_name": "accept-test.json", "payload": {}},
            headers=kb_headers,
        )
        # then：400 + 支持清单；前置校验先于建任务
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "支持" in body["message"] and "products.json" in body["message"]
        assert "Traceback" not in resp.text
        assert db_session.query(KbIngestTask).count() == 0

    def test_should_reject_unassembled_domain(self, client, seeded, kb_headers):
        # given：未装配领域
        # when
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"domain_id": "no-such-domain", "source_name": "products.json", "payload": {}},
            headers=kb_headers,
        )
        # then
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"
        assert "未装配入库适配器" in resp.json()["message"]

    def test_should_accept_conflict_scenarios_whitelisted_file(self, client, seeded, kb_headers):
        # given：白名单成员 conflict_scenarios.json（走冲突装载路径）
        payload = json.loads((MOCK_DIR / "conflict_scenarios.json").read_text(encoding="utf-8"))
        # when
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"domain_id": "chemical", "source_name": "conflict_scenarios.json", "payload": payload},
            headers=kb_headers,
        )
        assert resp.status_code == 202, resp.text
        task_id = resp.json()["data"]["task_id"]
        # then：eager 模式同步完成，冲突 5 条、无文档/分块
        status = client.get(f"/api/v1/knowledge/documents/{task_id}", headers=kb_headers).json()["data"]
        assert status["status"] == "ready"
        assert status["conflicts_detected"] == 5
        assert status["docs"] == 0
        assert status["chunks"] == 0
        assert status["pending_review"] is True


# ================================================================
# G 组：冲突处置措辞（F-08）
# ================================================================
def _conflict(
    conflict_id: str,
    topic: str,
    *,
    status: str = "open",
    conflict_type: str = "数值冲突",
    title: str = "冲突标题",
) -> ConflictRecord:
    return ConflictRecord(
        conflict_id=conflict_id,
        domain_id="chemical",
        title=title,
        topic=topic,
        internal_claim=ConflictClaims(source_type="internal", refs=["EXP-1"], statement="内部结论：温度160℃×90s", confidence=0.9),
        external_claim=ConflictClaims(source_type="external", refs=["EXT-1"], statement="外部观点：温度180℃×60s", confidence=0.6),
        conflict_type=conflict_type,
        expected_resolution={"must_note": "注明前提差异"},
        status=status,
    )


class TestConflictDisposalWording:
    def test_should_render_both_wording_branches_side_by_side(self):
        # given：同一 draft 两冲突（合规 / 非合规）
        draft = Draft(
            conflicts=[
                _conflict("CS-A", "环保合规", conflict_type="合规口径冲突"),
                _conflict("CS-B", "无氟防水耐洗性"),
            ]
        )
        # when
        blocks = _render_conflicts(draft)
        # then：分支互斥
        assert len(blocks) == 2
        assert "以权威外部法规为准（合规例外）" in blocks[0]
        assert "以内部为准" not in blocks[0]
        assert "以内部为准" in blocks[1]
        assert "以权威外部法规为准" not in blocks[1]

    def test_should_embed_compliance_exception_in_cs05_round_output(self, seeded, orchestrator, db_session, actor_expert, scenarios):
        # given：CS-05 全轮
        session = _new_session(db_session, actor_expert.user_id)
        # when
        outcome = _run_turn(orchestrator, session, scenarios["CS-05"]["demo_query"], actor_expert)
        # then：CS-05 冲突块含合规例外处置，且不套用内部优先模板
        header = "【冲突标注 · CS-05】"
        start = outcome.text.find(header)
        assert start != -1, "输出应含 CS-05 冲突标注块"
        rest = outcome.text[start + len(header) :]
        next_block = rest.find("【")
        block = rest if next_block == -1 else rest[:next_block]
        assert "以权威外部法规为准（合规例外）" in block
        assert "处置：以内部为准" not in block

    def test_should_skip_disposal_line_when_conflict_resolved(self):
        # given：resolved 的合规冲突
        draft = Draft(conflicts=[_conflict("CS-R", "环保合规", status="resolved")])
        # when
        blocks = _render_conflicts(draft)
        # then：无处置行，但标题/双方观点/must_note 照常
        block = blocks[0]
        assert "处置：" not in block
        assert "内部结论" in block and "外部观点" in block
        assert "裁决说明" in block

    def test_should_hit_compliance_branch_when_only_conflict_type_contains_keyword(self):
        # given：topic 不含、conflict_type 含「合规」
        draft = Draft(conflicts=[_conflict("CS-C", "PFAS限制", conflict_type="合规口径冲突")])
        # when
        blocks = _render_conflicts(draft)
        # then
        assert "以权威外部法规为准（合规例外）" in blocks[0]

    def test_should_not_hit_compliance_branch_when_only_title_contains_keyword(self):
        # given：仅 title 含「合规」（匹配口径只含 topic + conflict_type，防误伤扩展）
        draft = Draft(
            conflicts=[_conflict("CS-D", "无氟防水", conflict_type="数值冲突", title="合规讨论记录")]
        )
        # when
        blocks = _render_conflicts(draft)
        # then
        assert "以内部为准" in blocks[0]
        assert "以权威外部法规为准" not in blocks[0]


# ================================================================
# H 组：自定义指标（F-06）
# ================================================================
METRIC_FAMILIES = [
    "chat_ttft_seconds",
    "agent_node_duration_seconds",
    "agent_replan_total",
    "degrade_level_total",
    "llm_tokens_total",
    "llm_call_duration_seconds",
    "llm_failures_total",
    "hook_decisions_total",
    "conflict_detected_total",
    "solution_feedback_total",
    "citation_coverage_ratio",
    "eval_calibration_total",
]


class TestCustomMetrics:
    def test_should_expose_all_custom_families_when_enabled(self, metrics_enabled_settings):
        # given：临时开闸 settings 后自建应用（无样本也要有 TYPE 行）
        application = create_app("testing")
        # when
        with TestClient(application) as tc:
            resp = tc.get("/metrics")
        # then：12 个自定义族名 + instrumentator 默认族（挂接生效佐证）
        assert resp.status_code == 200
        text = resp.text
        for name in METRIC_FAMILIES:
            assert any(line.startswith(f"# TYPE {name} ") for line in text.splitlines()), f"缺少指标族 {name}"
        assert "# TYPE http_requests_total counter" in text

    def test_should_emit_samples_after_round_and_feedback(self, metrics_enabled_settings, seeded, db_session, expert_headers, scenarios):
        # given：metrics 应用 + seeded 同一 Session；进程级 REGISTRY 用前后快照差值
        application = create_app("testing")
        application.dependency_overrides[get_db] = lambda: db_session
        with TestClient(application) as tc:
            before = _metrics_text()
            # when：创建会话 → CS-05 一轮 SSE → 反馈 adopted
            session_id = tc.post(
                "/api/v1/chat/sessions", json={"domain_id": "chemical"}, headers=expert_headers
            ).json()["data"]["session_id"]
            resp = tc.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                json={"text": scenarios["CS-05"]["demo_query"]},
                headers=expert_headers,
            )
            assert resp.status_code == 200
            events = parse_sse(resp.text)
            assert any(name == "done" for name, _ in events)
            items = tc.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=expert_headers).json()["data"]["items"]
            assistant = next(m for m in items if m["role"] == "assistant")
            feedback = tc.post(
                f"/api/v1/chat/messages/{assistant['message_id']}/feedback",
                json={"rating": "adopted"},
                headers=expert_headers,
            )
            assert feedback.status_code == 200
            after = _metrics_text()
        # then：按族名 + 标签增量断言
        assert _metric_sum(after, "degrade_level_total", level="L0") - _metric_sum(before, "degrade_level_total", level="L0") >= 1
        assert (
            _metric_sum(after, "agent_node_duration_seconds_count", node="retrieve")
            - _metric_sum(before, "agent_node_duration_seconds_count", node="retrieve")
            >= 1
        )
        assert (
            _metric_sum(after, "conflict_detected_total", scenario="CS-05")
            - _metric_sum(before, "conflict_detected_total", scenario="CS-05")
            >= 1
        )
        assert _metric_sum(after, "hook_decisions_total") - _metric_sum(before, "hook_decisions_total") >= 2
        assert _metric_exists(after, "hook_decisions_total", stage="output")
        ratio = _metric_sum(after, "citation_coverage_ratio")
        assert 0 < ratio <= 1
        assert _metric_sum(after, "chat_ttft_seconds_count") - _metric_sum(before, "chat_ttft_seconds_count") >= 1
        assert (
            _metric_sum(after, "solution_feedback_total", rating="adopted")
            - _metric_sum(before, "solution_feedback_total", rating="adopted")
            >= 1
        )

    def test_should_increment_eval_calibration_when_evaluation_runs(self, seeded, db_session, registry):
        # given：进程级快照
        before = _metrics_text()
        # when：离线评估（校准通道记录 applied=false）
        engine = EvaluationEngine(db_session, gateway=OfflineModelGateway(), registry=registry)
        result = asyncio.run(
            engine.evaluate(
                {
                    "domain_id": "chemical",
                    "object_type": "solution",
                    "object_ref": {"demo_case": "demo-6608-eu", "products": ["TF-WR-6608"]},
                    "scenario": "出口合规敏感",
                },
                persist=True,
            )
        )
        after = _metrics_text()
        # then
        assert result["eval_id"]
        assert (
            _metric_sum(after, "eval_calibration_total", applied="false")
            - _metric_sum(before, "eval_calibration_total", applied="false")
            >= 1
        )

    def test_should_return_404_when_metrics_disabled(self, client):
        # given：默认 testing 应用（METRICS_ENABLED=False）
        # when / then
        assert client.get("/metrics").status_code == 404


# ================================================================
# I 组：回归脚本朴素基线 trap 判定（F-10，脚本闭包免测 → 规则真值表防语义漂移）
# ================================================================
class TestNaiveBaselineTrapRule:
    @staticmethod
    def _is_trap(order: dict[str, int], internal_refs: list[str], external_refs: list[str]) -> bool:
        """镜像 scripts/run_regression.py main() 内的 trap 判定语义（闭包不可导入，此处按同款语义固化）。"""
        internal_pos = [order[r] for r in internal_refs if r in order]
        external_pos = [order[r] for r in external_refs if r in order]
        return bool(external_pos and internal_pos and min(external_pos) < min(internal_pos))

    def test_should_match_trap_truth_table(self):
        # given：朴素基线排序 order（A 在前 B 在后）
        order = {"A": 0, "B": 1}
        # then：external 先 → trap；internal 先/相等/任一缺失/空 → 非 trap
        assert self._is_trap(order, ["B"], ["A"]) is True
        assert self._is_trap(order, ["A"], ["B"]) is False
        assert self._is_trap(order, ["B"], ["B"]) is False
        assert self._is_trap(order, ["B"], ["C"]) is False
        assert self._is_trap(order, ["C"], ["A"]) is False
        assert self._is_trap({}, ["A"], ["B"]) is False


# ================================================================
# J 组：快修包（D-22 认证异常收窄；A-09 哈希下沉兼容）
# ================================================================
class TestQuickFixSecurity:
    def test_should_return_false_and_log_when_verify_raises_unexpected(self, caplog):
        # 快修包③：未知异常不再静默——返回 False 且留痕（H-01）
        from core.admin import security
        from core.admin.security import verify_password

        class _BoomHasher:
            def verify(self, *_args, **_kwargs):
                raise RuntimeError("argon2 boom")

        with patch.object(security, "_hasher", _BoomHasher()):
            with caplog.at_level("ERROR"):
                assert verify_password("hash", "pw") is False
        assert any("unexpected" in record.message for record in caplog.records)

    def test_should_keep_legacy_import_path_for_password_helpers(self):
        # 快修包⑤：app.auth.services 再导出兼容既有导入路径，行为不变
        from app.auth.services import hash_password, verify_password

        hashed = hash_password("Test@12345")
        assert verify_password(hashed, "Test@12345") is True
        assert verify_password(hashed, "wrong-pass") is False
