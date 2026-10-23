"""领域插件注册表（M0/领域插件层，技术方案 02 §2.4）。

一个领域插件 = domain.yaml（配置）+ 领域打分器代码 + 提示词资产 + 工具契约 + 知识数据。
底座引擎一行不改即可挂载新领域；「换配置即换领域」= 换「配置 + 领域代码包」。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.orchestration.hooks import OPTIONAL_CAPABILITIES, REQUIRED_CAPABILITIES, has_capability

# 受信插件钩子白名单：用户提交的配置不能指定任意 import 路径（01 §4）
HOOK_MODULE_ALLOWLIST: dict[str, str] = {
    "chemical": "domains.chemical.hooks",
    "investment": "domains.investment.hooks",
}


class DomainPluginError(RuntimeError):
    """插件声明或实现不合法：阻止激活，而不是静默降级。"""


def load_domain_hooks(domain_id: str) -> Any:
    """按白名单加载钩子并校验能力声明；未列入白名单视为不支持可选能力（hooks=None）。"""
    module_path = HOOK_MODULE_ALLOWLIST.get(domain_id)
    if module_path is None:
        return None
    try:
        hooks = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001 - 统一转插件错误，保留原因链
        raise DomainPluginError(f"领域 {domain_id} 钩子加载失败: {exc}") from exc
    capabilities = getattr(hooks, "CAPABILITIES", None)
    if not isinstance(capabilities, frozenset) or not capabilities:
        raise DomainPluginError(f"领域 {domain_id} 钩子未声明 CAPABILITIES")
    unknown = capabilities - REQUIRED_CAPABILITIES - OPTIONAL_CAPABILITIES
    if unknown:
        raise DomainPluginError(f"领域 {domain_id} 声明了未知能力: {'、'.join(sorted(unknown))}")
    missing_required = REQUIRED_CAPABILITIES - capabilities
    if missing_required:
        raise DomainPluginError(f"领域 {domain_id} 缺少必备能力: {'、'.join(sorted(missing_required))}")
    for capability in capabilities:
        if not has_capability(hooks, capability):
            raise DomainPluginError(f"领域 {domain_id} 声明的能力无实现: {capability}")
    return hooks


@dataclass
class DomainPlugin:
    domain_id: str
    name: str
    version: str
    status: str
    base_dir: Path
    knowledge_binding: dict[str, Any] = field(default_factory=dict)
    eval_config: dict[str, Any] = field(default_factory=dict)
    agent_config: dict[str, Any] = field(default_factory=dict)
    toolset_ref: str = ""
    memory_policy: dict[str, Any] = field(default_factory=dict)
    hooks: Any = None  # 领域钩子模块（受信白名单加载；None=未声明可选能力）

    # ---------- 知识绑定 ----------
    @property
    def partition_order(self) -> list[str]:
        return list(self.knowledge_binding.get("default_partition_order", ["internal", "external"]))

    @property
    def fusion_priority(self) -> list[str]:
        return list(
            self.knowledge_binding.get(
                "fusion_priority", ["confirmed", "internal", "stated", "external", "ai_inferred"]
            )
        )

    @property
    def compliance_override_enabled(self) -> bool:
        return bool(self.knowledge_binding.get("compliance_override", {}).get("enabled", False))

    @property
    def compliance_category_tag(self) -> str:
        return self.knowledge_binding.get("compliance_override", {}).get("category_tag", "compliance")

    def collection_for(self, source_type: str) -> dict[str, Any]:
        for coll in self.knowledge_binding.get("collections", []):
            if source_type in coll.get("source_types", []):
                return coll
        return {}

    # ---------- 编排/评估 ----------
    @property
    def clarify_slots(self) -> list[str]:
        return list(self.agent_config.get("clarify_slots", []))

    @property
    def constraints(self) -> list[str]:
        return list(self.agent_config.get("constraints", []))

    @property
    def confidence_threshold(self) -> float:
        return float(self.agent_config.get("confidence_threshold", 0.6))

    @property
    def routing_keywords(self) -> dict[str, list[str]]:
        return dict(self.agent_config.get("routing_keywords", {}))

    @property
    def dimensions(self) -> list[dict[str, Any]]:
        return list(self.eval_config.get("dimensions", []))

    @property
    def scenario_profiles(self) -> dict[str, dict[str, int]]:
        return {
            name: {k: int(v) for k, v in weights.items()}
            for name, weights in self.eval_config.get("scenario_profiles", {}).items()
        }

    @property
    def standard_ref(self) -> str:
        return str(self.eval_config.get("standard_ref", ""))

    def resolve_scorer(self, scorer_id: str):
        """解析 `chemical.d1_performance` → 可调用（打分器注册表为纯 Python 函数，决策 g）。

        约定：`{domain_id}.{func_name}`，实现位于 `domains.{domain_id}.evaluation.scorers`；
        形如 `chemical.sub.func` 的三段式指向 `domains.{domain_id}.{sub}` 模块。
        """
        parts = scorer_id.split(".")
        if len(parts) < 2:
            raise ValueError(f"scorer id 非法: {scorer_id}")
        func_name = parts[-1]
        module_path = parts[:-1]
        if len(module_path) == 1:
            module = importlib.import_module(f"domains.{self.domain_id}.evaluation.scorers")
        else:
            module = importlib.import_module(".".join(["domains", *module_path]))
        func = getattr(module, func_name, None)
        if func is None:
            raise ValueError(f"scorer 未注册: {scorer_id}")
        return func

    def prompt_text(self, ref: str) -> str:
        path = self.base_dir / ref
        return path.read_text(encoding="utf-8") if path.exists() else ""


class DomainRegistry:
    def __init__(self, plugins: dict[str, DomainPlugin]):
        self._plugins = plugins

    @classmethod
    def load_default(cls, root: Path | None = None) -> DomainRegistry:
        base = root or Path("domains")
        plugins: dict[str, DomainPlugin] = {}
        if base.exists():
            for yaml_file in sorted(base.glob("*/domain.yaml")):
                config = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                plugin = DomainPlugin(
                    domain_id=config.get("domain_id", yaml_file.parent.name),
                    name=config.get("name", ""),
                    version=str(config.get("version", "0.1.0")),
                    status=config.get("status", "draft"),
                    base_dir=yaml_file.parent,
                    knowledge_binding=config.get("knowledge_binding", {}),
                    eval_config=config.get("eval_config", {}),
                    agent_config=config.get("agent_config", {}),
                    toolset_ref=config.get("toolset_ref", ""),
                    memory_policy=config.get("memory_policy", {}),
                )
                plugin.hooks = load_domain_hooks(plugin.domain_id)
                plugins[plugin.domain_id] = plugin
        registry = cls(plugins)
        registry._mount_toolsets()
        return registry

    def _mount_toolsets(self) -> None:
        """导入领域工具模块（@tool 装饰器进程内注册；缺失工具集不阻断）。"""
        for plugin in self._plugins.values():
            try:
                importlib.import_module(f"domains.{plugin.domain_id}.toolset")
            except ImportError:
                continue

    def get(self, domain_id: str) -> DomainPlugin | None:
        return self._plugins.get(domain_id)

    def require(self, domain_id: str) -> DomainPlugin:
        plugin = self.get(domain_id)
        if plugin is None:
            raise KeyError(f"领域插件未装配: {domain_id}")
        return plugin

    def all(self) -> list[DomainPlugin]:
        return list(self._plugins.values())

    def online(self) -> list[DomainPlugin]:
        return [p for p in self._plugins.values() if p.status == "online"]

    def register(self, plugin: DomainPlugin) -> None:
        if plugin.hooks is None:
            plugin.hooks = load_domain_hooks(plugin.domain_id)
        self._plugins[plugin.domain_id] = plugin
