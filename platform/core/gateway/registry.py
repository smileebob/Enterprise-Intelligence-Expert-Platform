"""工具注册表（MCP 同构契约，技术方案 02 §2.5）。

@tool 装饰器进程内注册（名称/描述/JSON Schema 入参/权限声明/风险等级/返回结构）；
生产需要时可将注册表整体桥接为 MCP server，编排侧零改动。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.abstraction.dtos import ToolSpec

_RATE_RE = re.compile(r"^(\d+)/(second|minute|hour|m|s|h)$")


@dataclass
class ToolDefinition:
    spec: ToolSpec
    func: Callable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        permission: str,
        *,
        input_schema: dict[str, Any] | None = None,
        risk_level: str = "normal",
        domain: str = "",
        rate_limit: str | None = None,
    ):
        def decorator(func: Callable):
            spec = ToolSpec(
                name=name,
                description=description,
                input_schema=input_schema or {"type": "object", "properties": {}},
                permission=permission,
                risk_level=risk_level,
                domain=domain,
                rate_limit=rate_limit,
            )
            self._tools[name] = ToolDefinition(spec=spec, func=func)
            return func

        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def for_domain(self, domain_id: str) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.spec.domain == domain_id]


def parse_rate_limit(rule: str | None) -> tuple[int, int] | None:
    """'10/minute' → (10, 60)；None/非法返回 None（default 60/minute 由调用方处理）。"""
    if not rule:
        return None
    match = _RATE_RE.match(rule.strip())
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2)
    seconds = {"second": 1, "s": 1, "minute": 60, "m": 60, "hour": 3600, "h": 3600}[unit]
    return count, seconds


tool_registry = ToolRegistry()
tool = tool_registry.register
