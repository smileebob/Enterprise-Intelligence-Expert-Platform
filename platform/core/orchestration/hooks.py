"""领域插件钩子协议（01 · 领域插件钩子抽象）。

引擎只依赖本文件的类型化协议；具体领域通过 `domains/{id}/hooks.py` 显式声明能力并实现。
时序：plan_tools（检索前，只产意图）→ bind_tool_args（检索后绑定证据）→ 工具执行 →
evidence_refs_from_result（结果补证）→ assemble_evaluation → persist_outcome → compose。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# 领域必须具备的能力；其余为可选能力，未声明时引擎按 no-op 处理
REQUIRED_CAPABILITIES = frozenset({"compose"})
OPTIONAL_CAPABILITIES = frozenset(
    {"plan_tools", "bind_tool_args", "evidence_refs_from_result", "assemble_evaluation", "persist_outcome"}
)


@dataclass(frozen=True)
class PluginSnapshot:
    """回合内固定的插件快照（配置切换不影响进行中的回合）。"""

    domain_id: str
    version: str
    code_version: str = ""
    config_rev: int = 0


@dataclass(frozen=True)
class ToolIntent:
    """规划产物：工具名 + 静态参数 + 稳定调用序号（证据相关参数由 bind 阶段补齐）。"""

    tool_name: str
    static_args: dict[str, Any] = field(default_factory=dict)
    invocation_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "static_args": dict(self.static_args),
            "invocation_index": self.invocation_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolIntent:
        return cls(
            tool_name=str(data.get("tool_name", "")),
            static_args=dict(data.get("static_args") or {}),
            invocation_index=int(data.get("invocation_index", 0)),
        )


@dataclass(frozen=True)
class NeedView:
    """需求只读视图（不含证据，供 plan 阶段使用）。"""

    scenario: str = "默认"
    market: str = "unknown"
    mode: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    target_performance: tuple[str, ...] = ()
    product_ids: tuple[str, ...] = ()
    user_msg: str = ""


@dataclass(frozen=True)
class EvidenceView:
    """检索完成后的证据视图（ToolIntent → 具体参数）。"""

    need: NeedView
    evidence: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ToolResultView:
    tool_name: str
    ok: bool
    data: Any = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnView:
    """评估装配所需的回合只读视图。"""

    session_id: str
    turn_id: str
    domain_id: str
    need: NeedView
    evidence: tuple[Any, ...] = ()
    tool_results: tuple[ToolResultView, ...] = ()


@dataclass
class EvalRequestSpec:
    object_type: str
    object_ref: dict[str, Any]
    scenario: str
    need_ref: str = ""


@dataclass(frozen=True)
class ExecutionContext:
    """稳定执行上下文：effect_key = run_id + node + invocation_index（05-B 复用同一键）。"""

    session_id: str
    run_id: str
    message_id: str
    domain_id: str
    node: str = "evaluate"
    invocation_index: int = 0

    @property
    def effect_key(self) -> str:
        return f"{self.run_id}:{self.node}:{self.invocation_index}"


@dataclass
class GenerationContext:
    """生成阶段上下文（已审核约束/偏好由 06-B 后续注入）。"""

    need: Any
    fused: Any
    eval_result: dict[str, Any] | None = None
    tool_results: tuple[dict[str, Any], ...] = ()
    degrade_level: str = "L0"


class DomainHooks(Protocol):
    CAPABILITIES: frozenset[str]
    CODE_VERSION: str

    def plan_tools(self, need: NeedView, snapshot: PluginSnapshot) -> list[ToolIntent]: ...

    def bind_tool_args(
        self, intent: ToolIntent, evidence: EvidenceView, snapshot: PluginSnapshot
    ) -> dict[str, Any]: ...

    def evidence_refs_from_result(
        self, intent: ToolIntent, result: ToolResultView, snapshot: PluginSnapshot
    ) -> list[str]: ...

    def assemble_evaluation(self, turn: TurnView, snapshot: PluginSnapshot) -> EvalRequestSpec | None: ...

    def persist_outcome(
        self, db: Any, ctx: ExecutionContext, spec: EvalRequestSpec, snapshot: PluginSnapshot
    ) -> EvalRequestSpec: ...

    def compose(self, ctx: GenerationContext, snapshot: PluginSnapshot) -> Any: ...


def has_capability(hooks: Any, name: str) -> bool:
    """能力既要在清单中声明，也要有可调用实现（声明缺失即视为不支持）。"""
    return (
        hooks is not None
        and name in getattr(hooks, "CAPABILITIES", frozenset())
        and callable(getattr(hooks, name, None))
    )
