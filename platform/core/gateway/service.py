"""工具与权限网关（M5，技术方案 03 §3.5）：调用收口点。

invoke() 是所有工具调用的唯一入口：Hook③（权限校验/危险拦截/限流）+ 全量审计 +
结果来源标注（source_type + 获取时间）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.abstraction.dtos import Actor, ToolResult
from core.abstraction.enums import HookAction, RiskLevel
from core.abstraction.errors import ToolDeniedError
from core.cache import cache_get, cache_set, ckey
from core.gateway.permissions import role_permissions
from core.gateway.registry import ToolDefinition, parse_rate_limit, tool_registry
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit
from core.guardrails.masking import mask_text_for_role

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = (60, 60)  # 每工具每分钟 60 次


@dataclass
class ToolCtx:
    """工具执行上下文（调用方/会话/领域/依赖）。"""

    db: Session
    actor: Actor | None = None
    session_id: str = ""
    domain_id: str = ""
    trace_id: str = ""
    gateway: Any = None  # ModelGateway
    registry: Any = None  # DomainRegistry
    extra: dict[str, Any] = field(default_factory=dict)


class ToolGateway:
    def __init__(self, db: Session, registry=None):
        self.db = db
        self.tools = registry or tool_registry

    # ---------------- 授权查询 ----------------
    def list_available(self, role_id: str, domain_id: str) -> list[dict[str, Any]]:
        perms = role_permissions(self.db, role_id)
        specs = []
        for definition in self.tools.all():
            spec = definition.spec
            if spec.domain and spec.domain != domain_id:
                continue
            if spec.permission in perms:
                specs.append(
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "risk_level": spec.risk_level,
                        "input_schema": spec.input_schema,
                    }
                )
        return specs

    # ---------------- 调用收口 ----------------
    async def invoke(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolCtx,
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        definition: ToolDefinition | None = self.tools.get(name)
        hook_ctx = HookContext(
            stage="tool",
            actor=ctx.actor,
            domain_id=ctx.domain_id,
            session_id=ctx.session_id,
            payload={"tool": name, "args": args},
            trace_id=ctx.trace_id,
        )
        if definition is None:
            raise ToolDeniedError(f"工具不存在: {name}", code="NOT_FOUND")

        spec = definition.spec
        role_id = ctx.actor.role_id if ctx.actor else ""
        # ① 权限校验（RBAC：角色 × 领域 × 工具）
        if spec.permission not in role_permissions(self.db, role_id):
            decision = HookDecision(action=HookAction.BLOCK, code="PERM_DENIED", message=f"无权限调用工具 {name}")
            write_hook_audit(self.db, hook_ctx, decision, action="hook.tool.block", detail={"reason": "permission"})
            self.db.commit()
            raise ToolDeniedError(f"无权限调用工具 {name}")

        # ② 危险操作二次确认（interrupt 对接）
        if spec.risk_level == RiskLevel.DANGEROUS and not confirmed:
            decision = HookDecision(
                action=HookAction.CONFIRM,
                code="TOOL_CONFIRM_REQUIRED",
                message=f"危险操作需要二次确认：{spec.description}",
                extra={"card": {"tool": name, "args_preview": args, "risk": "dangerous"}},
            )
            write_hook_audit(self.db, hook_ctx, decision, action="hook.tool.confirm", detail={"args": args})
            self.db.commit()
            return ToolResult(ok=False, message=decision.message, meta={"confirm_required": True, "card": decision.extra["card"]})

        # ③ 限流（按工具 × 主体，服务端速率）
        allowed, retry_after = await self._check_rate_limit(spec.name, spec.rate_limit, ctx)
        if not allowed:
            decision = HookDecision(action=HookAction.BLOCK, code="RATE_LIMITED", message=f"工具 {name} 调用过于频繁")
            write_hook_audit(self.db, hook_ctx, decision, action="hook.tool.block", detail={"reason": "rate_limit"})
            self.db.commit()
            return ToolResult(ok=False, message=decision.message, meta={"retry_after": retry_after})

        # ④ 执行 + 计时
        started = time.perf_counter()
        try:
            result = await definition.func(ctx, **args)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            decision = HookDecision(action=HookAction.PASS, code="INTERNAL_ERROR", message=str(exc))
            write_hook_audit(
                self.db, hook_ctx, decision, action="hook.tool.error",
                detail={"duration_ms": duration_ms, "error": str(exc)},
            )
            self.db.commit()
            logger.warning("tool %s failed", name, exc_info=True)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)

        # ⑤ 结果来源标注（FR-5-04）+ 全量审计（参数摘要脱敏）
        if isinstance(result, ToolResult) and not result.meta.get("fetched_at"):
            from core.clock import iso, utc_now

            result.meta.setdefault("fetched_at", iso(utc_now()))
        if isinstance(result, ToolResult) and not result.meta.get("source_type"):
            result.meta.setdefault("source_type", "internal")

        decision = HookDecision(action=HookAction.PASS, payload={})
        write_hook_audit(
            self.db,
            hook_ctx,
            decision,
            action="tool.invoke",
            detail={
                "tool": name,
                "args_summary": self._mask_args(args, role_id),
                "ok": result.ok if isinstance(result, ToolResult) else False,
                "duration_ms": duration_ms,
                "result_summary": self._summarize(result),
            },
        )
        self.db.commit()
        return result

    async def _check_rate_limit(self, tool_name: str, rule: str | None, ctx: ToolCtx) -> tuple[bool, int]:
        limit, window = parse_rate_limit(rule) or DEFAULT_RATE_LIMIT
        actor_id = ctx.actor.user_id if ctx.actor else "anonymous"
        window_index = int(time.time()) // window
        key = ckey("tool", "rl", tool_name, actor_id, str(window_index))
        current = await cache_get(key) or 0
        if current >= limit:
            return False, window - int(time.time()) % window
        await cache_set(key, current + 1, ttl=window + 1)
        return True, 0

    def _mask_args(self, args: dict[str, Any], role_id: str) -> dict[str, Any]:
        masked: dict[str, Any] = {}
        for key, value in (args or {}).items():
            if key.startswith("_"):
                continue  # 内部参数不入审计
            text = str(value)
            masked[key] = mask_text_for_role(text, role_id, ["配方配比", "报价", "客户联系方式"])[0][:200]
        return masked

    def _summarize(self, result: Any) -> str:
        if not isinstance(result, ToolResult):
            return str(result)[:120]
        if isinstance(result.data, list):
            return f"{len(result.data)} 条"
        return str(result.data)[:120]
