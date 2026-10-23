"""Phase 14 · 01 领域插件钩子契约（H01/H03/H04/H06/H07 的离线切片）。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import core.domains as domains_module
from core.abstraction.dtos import Evidence
from core.domains import DomainPluginError, DomainRegistry, load_domain_hooks
from core.orchestration.hooks import (
    EvidenceView,
    ExecutionContext,
    NeedView,
    PluginSnapshot,
    ToolResultView,
    has_capability,
)
from core.orchestration.state import TurnState


@pytest.fixture()
def registry() -> DomainRegistry:
    return DomainRegistry.load_default()


@pytest.fixture()
def orchestrator(db_session):
    from core.orchestration.engine import Orchestrator

    return Orchestrator(db_session)


def _chemical_evidence() -> tuple[Evidence, ...]:
    return (
        Evidence(
            chunk_id="chk:products:TF-WR-6608",
            content="产品[TF-WR-6608] 类别 防水剂",
            source_type="internal",
            confidence=0.9,
            citation_id="C1",
            meta={
                "record_type": "products",
                "record_id": "TF-WR-6608",
                "fields": {"category": "防水剂", "process_ref": "PROC-01"},
            },
        ),
        Evidence(
            chunk_id="chk:products:TF-WR-6609",
            content="产品[TF-WR-6609] 类别 防水剂",
            source_type="internal",
            confidence=0.8,
            citation_id="C2",
            meta={"record_type": "products", "record_id": "TF-WR-6609", "fields": {"category": "防水剂"}},
        ),
    )


class TestHookPlanning:
    def test_should_plan_tools_without_evidence_and_bind_after_retrieval(self):
        hooks = load_domain_hooks("chemical")
        need = NeedView(
            scenario="诊断",
            market="eu",
            mode="diagnostic",
            slots={"__mode__": "diagnostic"},
            target_performance=("防水",),
            user_msg="黄变问题怎么改进",
        )
        snapshot = PluginSnapshot(domain_id="chemical", version="0.1.0", code_version=hooks.CODE_VERSION)

        intents = hooks.plan_tools(need, snapshot)
        names = [intent.tool_name for intent in intents]
        assert names == ["check_compliance", "search_experiments", "search_products"]
        assert [intent.invocation_index for intent in intents] == [0, 1, 2]
        # 规划阶段不携带证据相关参数
        compliance = intents[0]
        assert "product_ids" not in compliance.static_args

        args = hooks.bind_tool_args(compliance, EvidenceView(need=need, evidence=_chemical_evidence()), snapshot)
        assert args["product_ids"] == ["TF-WR-6608", "TF-WR-6609"]
        assert args["target_market"] == "出口欧盟"

        refs = hooks.evidence_refs_from_result(
            intents[2],
            ToolResultView("search_products", True, [{"product_id": "TF-WR-6608"}, {"product_id": "TF-WR-6609"}]),
            snapshot,
        )
        assert refs == ["TF-WR-6608", "TF-WR-6609"]

    def test_should_assemble_chemical_eval_spec_with_products_and_links(self):
        hooks = load_domain_hooks("chemical")
        snapshot = PluginSnapshot(domain_id="chemical", version="0.1.0", code_version=hooks.CODE_VERSION)
        need = NeedView(scenario="出口合规敏感", market="eu", target_performance=("防水",))
        from core.orchestration.hooks import TurnView

        spec = hooks.assemble_evaluation(
            TurnView(session_id="S", turn_id="T", domain_id="chemical", need=need, evidence=_chemical_evidence()),
            snapshot,
        )
        assert spec is not None
        assert spec.object_type == "solution"
        assert spec.object_ref["products"] == ["TF-WR-6608", "TF-WR-6609"]
        assert spec.object_ref["process"] == "PROC-01"
        assert spec.object_ref["target_market"] == "出口欧盟"


class TestHookCapabilities:
    def test_should_declare_only_composer_for_investment(self):
        hooks = load_domain_hooks("investment")
        assert hooks.CAPABILITIES == frozenset({"compose"})
        assert has_capability(hooks, "compose") is True
        assert has_capability(hooks, "plan_tools") is False
        assert has_capability(hooks, "assemble_evaluation") is False

    def test_should_block_activation_when_declared_module_lacks_capabilities(self, monkeypatch):
        monkeypatch.setitem(domains_module.HOOK_MODULE_ALLOWLIST, "broken", "domains.chemical.composer")
        with pytest.raises(DomainPluginError):
            load_domain_hooks("broken")

    def test_should_block_activation_when_declared_module_import_fails(self, monkeypatch):
        monkeypatch.setitem(domains_module.HOOK_MODULE_ALLOWLIST, "broken", "domains.no_such_hooks")
        with pytest.raises(DomainPluginError):
            load_domain_hooks("broken")

    def test_should_keep_no_hooks_when_domain_not_allowlisted(self):
        assert load_domain_hooks("third-party-domain") is None


class TestHookSnapshotAndEffects:
    def test_should_keep_snapshot_immutable_within_turn(self, orchestrator, registry):
        state = TurnState(session_id="S-snap", turn_id="T-snap", domain_id="chemical")
        plugin = registry.get("chemical")
        original_version = plugin.version
        snapshot = orchestrator._snapshot(plugin, state)
        plugin.version = "9.9.9"
        try:
            again = orchestrator._snapshot(plugin, state)
        finally:
            plugin.version = original_version
        assert again.version == snapshot.version
        assert again.code_version == snapshot.code_version

    def test_should_derive_stable_effect_key_from_run_node_and_index(self):
        ctx = ExecutionContext(
            session_id="S", run_id="T-1", message_id="M-1", domain_id="chemical", node="evaluate", invocation_index=2
        )
        assert ctx.effect_key == "T-1:evaluate:2"
        assert ctx.effect_key == ExecutionContext(
            session_id="S", run_id="T-1", message_id="M-1", domain_id="chemical", node="evaluate", invocation_index=2
        ).effect_key

    def test_should_not_import_or_special_case_domains_in_engine(self):
        source = Path("core/orchestration/engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith("domains.") for name in imported)
        assert "importlib" not in imported
        for forbidden in ("check_compliance", "search_products", "search_experiments", "chemical", "investment"):
            assert forbidden not in source


class TestRegistryHooks:
    def test_should_attach_hooks_when_registry_loads_domains(self):
        registry = DomainRegistry.load_default()
        for domain_id in ("chemical", "investment"):
            plugin = registry.get(domain_id)
            assert plugin is not None
            assert plugin.hooks is not None
        assert registry.get("chemical").hooks.CODE_VERSION == "chemical-hooks-v1"
