"""招商领域钩子适配器：显式声明仅支持 composer（不提供工具规划/实时评估/持久化）。"""

from __future__ import annotations

from typing import Any

from core.orchestration.hooks import GenerationContext, PluginSnapshot

CODE_VERSION = "investment-hooks-v1"

CAPABILITIES = frozenset({"compose"})


def compose(ctx: GenerationContext, snapshot: PluginSnapshot) -> Any:
    from domains.investment.composer import compose as investment_compose

    return investment_compose(
        ctx.need,
        ctx.fused,
        eval_result=ctx.eval_result,
        tool_results=list(ctx.tool_results),
        degrade_level=ctx.degrade_level,
    )
