"""Phase 19 · 04 上下文预算与工具输出投影（C02/C03 切片）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.abstraction.dtos import Actor, ToolResult
from core.abstraction.enums import Roles
from core.gateway.registry import tool
from core.orchestration.context import project_tool_data, trim_history
from core.orchestration.engine import Orchestrator
from core.orchestration.hooks import ToolIntent
from core.orchestration.state import Need, TurnState
from core.runs.service import RunService

BIG_ITEMS = [
    {"product_id": f"TF-{index:04d}", "note": "耐洗防水" * 200, "price": index}
    for index in range(100)
]


@tool(
    name="big_result_test",
    description="返回超限结果的测试工具（仅单测注册）",
    permission="tool.chemical.search",
    domain="chemical",
    input_schema={"type": "object", "properties": {"count": {"type": "integer"}}, "required": []},
)
async def big_result_test(ctx, count: int = 100) -> ToolResult:
    return ToolResult(ok=True, data=BIG_ITEMS[:count], meta={"source_type": "internal"})


@pytest.fixture()
def registry():
    from core.domains import DomainRegistry

    return DomainRegistry.load_default()


def _expert() -> Actor:
    return Actor(
        user_id="u-expert", role_id=Roles.EXPERT, permission_max=2, domain_scope=["chemical"]
    )


class TestProjection:
    def test_should_keep_small_result_unchanged(self):
        data = [{"id": 1}, {"id": 2}]
        view, meta = project_tool_data(data, inline_bytes=16384)
        assert view == data
        assert meta["truncated"] is False

    def test_should_bound_list_and_string_fields_when_over_limit(self):
        view, meta = project_tool_data(BIG_ITEMS, inline_bytes=1024)
        assert meta["truncated"] is True
        assert len(view) == 5
        assert meta["total_count"] == 100
        assert all(len(item["note"]) <= 500 for item in view)
        assert meta["original_bytes"] > 1024

    def test_should_bound_dict_keys_and_nested_lists(self):
        data = {f"k{index}": list(range(20)) for index in range(40)}
        view, meta = project_tool_data(data, inline_bytes=128)
        assert meta["truncated"] is True
        assert len(view) == 20
        assert len(view["k0"]) == 3

    def test_should_measure_multibyte_by_bytes(self):
        text = "防" * 6000  # 18000 bytes UTF-8
        view, meta = project_tool_data(text, inline_bytes=16384)
        assert meta["truncated"] is True
        assert len(view.encode("utf-8")) <= 1000 * 3
        assert view == "防" * 1000


class TestHistoryBudget:
    def test_should_keep_recent_messages_within_budget(self):
        messages = [{"role": "user", "content": "x" * 1000} for _ in range(10)]
        kept = trim_history(messages, max_bytes=3000)
        assert len(kept) == 3
        assert kept == messages[-3:]

    def test_should_truncate_single_oversized_message(self):
        kept = trim_history([{"role": "user", "content": "防" * 5000}], max_bytes=2048)
        assert len(kept) == 1
        assert len(kept[0]["content"].encode("utf-8")) <= 2048


class TestEngineOffload:
    def test_should_offload_large_tool_result_and_keep_bounded_view(self, seeded, registry):
        run = RunService(seeded).create_run(
            session_id="S-context", owner_id="u-expert", domain_id="chemical"
        )
        orchestrator = Orchestrator(seeded, registry=registry)
        state = TurnState(
            session_id="S-context",
            turn_id="T-context",
            run_id=run.run_id,
            user_id="u-expert",
            role_id=Roles.EXPERT,
            domain_id="chemical",
            need=Need(raw_text="检索大量产品"),
            plan={"tool_intents": [ToolIntent("big_result_test", {"count": 100}).to_dict()]},
        )

        async def noop(kind, data):
            return None

        asyncio.run(orchestrator._node_tools(state, registry.get("chemical"), _expert(), noop))
        result = state.plan["tool_results"][0]
        assert len(result["data"]) == 5
        assert result["meta"]["truncated"] is True
        payload_ref = result["meta"]["offloaded_payload_ref"]
        assert payload_ref
        full = json.loads(RunService(seeded).load_payload(payload_ref, _expert()).decode("utf-8"))
        assert len(full) == 100
        events = RunService(seeded).list_events(run.run_id, _expert(), limit=100)
        assert any(event["event_type"] == "context.offloaded" for event in events)
        returned = next(event for event in events if event["event_type"] == "tool.returned")
        assert returned["metadata"]["offloaded_payload_ref"] == payload_ref
        assert returned["metadata"]["truncated"] is True
