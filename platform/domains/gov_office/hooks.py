"""政务办公领域钩子：首期只提供结构化草稿组装能力。"""

from __future__ import annotations

from typing import Any

from core.orchestration.hooks import GenerationContext, PluginSnapshot

CODE_VERSION = "gov-office-hooks-v1"

CAPABILITIES = frozenset({"compose"})


def compose(ctx: GenerationContext, snapshot: PluginSnapshot) -> Any:
    from domains.gov_office.composer import compose as gov_compose

    return gov_compose(
        ctx.need,
        ctx.fused,
        eval_result=ctx.eval_result,
        tool_results=list(ctx.tool_results),
        degrade_level=ctx.degrade_level,
    )
