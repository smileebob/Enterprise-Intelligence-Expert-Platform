"""工具与权限网关（M5，技术方案 03 §3.5）：调用收口点。

invoke() 是所有工具调用的唯一入口：Hook③（权限校验/危险拦截/限流）+ 全量审计 +
结果来源标注（source_type + 获取时间）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from core.abstraction.dtos import Actor, ToolResult
from core.abstraction.enums import HookAction, RiskLevel
from core.abstraction.errors import ToolDeniedError
from core.cache import cache_incr, ckey
from core.gateway.permissions import role_permissions
from core.gateway.registry import ToolDefinition, parse_rate_limit, tool_registry
from core.gateway.validation import validate_tool_args
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit
from core.guardrails.masking import mask_text_for_role

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = (60, 60)  # 每工具每分钟 60 次

# fork/sandbox 只允许只读工具；score_solution 等有落库副作用，拒绝
SANDBOX_ALLOWED_TOOLS = {
    "search_products",
    "search_formulas",
    "search_experiments",
    "search_external",
    "search_enterprises",
    "match_park_resources",
    "verify_qualification",
    "check_compliance",
}


@dataclass
class ToolCtx:
    """工具执行上下文（调用方/会话/领域/依赖）。"""

    db: Session
    actor: Actor | None = None
    session_id: str = ""
    domain_id: str = ""
    trace_id: str = ""
    run_id: str = ""
    node: str = "tool"
    invocation_index: int = 0
    approval_id: str = ""
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
        from core.runs.approvals import (
            ApprovalConflict,
            ApprovalExpired,
            ApprovalNotFound,
            ApprovalService,
        )
        from core.runs.effects import EffectLedger
        from core.runs.outbox import OutboxService

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
        if ctx.extra.get("sandbox") and name not in SANDBOX_ALLOWED_TOOLS:
            self._deny(hook_ctx, "SANDBOX_WRITE_BLOCKED", f"分叉试验禁止调用有副作用工具 {name}", "sandbox")
        # ⓪ 领域边界：工具声明域 == 执行域；主体域范围覆盖执行域（空范围=无准入）
        if spec.domain and ctx.domain_id and spec.domain != ctx.domain_id:
            self._deny(hook_ctx, "DOMAIN_MISMATCH", f"工具 {name} 不属于领域 {ctx.domain_id}", "domain")
        if ctx.actor is not None and ctx.domain_id and ctx.domain_id not in (ctx.actor.domain_scope or []):
            self._deny(hook_ctx, "DOMAIN_FORBIDDEN", "无权在该领域执行工具", "domain_scope")

        # ⓪' 参数契约：dict 结构 + 体积上限 + JSON Schema 子集校验
        if not isinstance(args, dict):
            self._deny(hook_ctx, "INVALID_TOOL_ARGS", f"工具 {name} 参数必须为对象", "args_type")
        raw_size = len(json.dumps(args, ensure_ascii=False, default=str).encode("utf-8"))
        if raw_size > get_settings().TOOL_ARGS_MAX_BYTES:
            self._deny(hook_ctx, "INVALID_TOOL_ARGS", f"工具 {name} 参数体积超过上限", "args_size")
        arg_errors = validate_tool_args(args, spec.input_schema)
        if arg_errors:
            self._deny(hook_ctx, "INVALID_TOOL_ARGS", f"工具 {name} 参数不合法：{'；'.join(arg_errors)}", "args_schema")

        # ① 权限校验（RBAC：角色 × 领域 × 工具；安全边界实时读 DB，跨进程撤权即时生效）
        if ctx.actor is not None:
            from core.admin.models import GovUser

            user = self.db.get(GovUser, ctx.actor.user_id)
            if user is None or user.status != "active":
                self._deny(hook_ctx, "AUTH_INVALID", "账号不可用", "account_status")
        if spec.permission not in role_permissions(self.db, role_id, fresh=True):
            decision = HookDecision(action=HookAction.BLOCK, code="PERM_DENIED", message=f"无权限调用工具 {name}")
            write_hook_audit(self.db, hook_ctx, decision, action="hook.tool.block", detail={"reason": "permission"})
            self.db.commit()
            raise ToolDeniedError(f"无权限调用工具 {name}")

        # ② 危险操作一次性审批（06-A2）：confirmed=true 不再是授权凭证
        dangerous_effect = None
        if spec.risk_level == RiskLevel.DANGEROUS:
            effect_key = str(
                ctx.extra.get("effect_key")
                or f"{ctx.run_id or ctx.trace_id or 'run'}:tool:{ctx.invocation_index}:{name}"
            )
            preview = self._mask_args(args, role_id)
            if not ctx.approval_id:
                approval = ApprovalService(self.db).create_or_get(
                    run_id=ctx.run_id or ctx.trace_id or "",
                    owner_id=ctx.actor.user_id if ctx.actor else "anonymous",
                    domain_id=ctx.domain_id,
                    session_id=ctx.session_id,
                    effect_key=effect_key,
                    tool_name=name,
                    tool_version="1",
                    args=args,
                    preview=preview,
                    actor_role=role_id,
                )
                decision = HookDecision(
                    action=HookAction.CONFIRM,
                    code="TOOL_CONFIRM_REQUIRED",
                    message=f"危险操作需要审批：{spec.description}",
                    extra={"card": {"tool": name, "args_preview": preview, "risk": "dangerous"}},
                )
                write_hook_audit(
                    self.db,
                    hook_ctx,
                    decision,
                    action="approval.requested",
                    detail={"approval_id": approval.approval_id, "effect_key": effect_key},
                )
                self.db.commit()
                return ToolResult(
                    ok=False,
                    message=decision.message,
                    meta={
                        "confirm_required": True,
                        "approval_id": approval.approval_id,
                        "effect_key": effect_key,
                        "card": decision.extra["card"],
                    },
                )
            try:
                ledger = EffectLedger(self.db)
                effect = ledger.get(effect_key)
                if effect is not None and effect.status in ("dispatched", "succeeded"):
                    raise ApprovalConflict("该 effect 已派发，不能重复执行")
                ApprovalService(self.db).consume(
                    ctx.approval_id,
                    effect_key=effect_key,
                    tool_name=name,
                    args=args,
                    actor=ctx.actor,
                )
                dangerous_effect = ledger.prepare(
                    effect_key,
                    run_id=ctx.run_id or ctx.trace_id or "",
                    node=ctx.node,
                    invocation_index=ctx.invocation_index,
                    operation=name,
                    request_digest=hashlib.sha256(
                        json.dumps(args, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest(),
                    approval_id=ctx.approval_id,
                )
                ledger.mark(dangerous_effect, "dispatched")
                OutboxService(self.db).enqueue(
                    run_id=ctx.run_id or ctx.trace_id or "",
                    effect_key=effect_key,
                    operation=name,
                )
                # 审批消费 + effect dispatched + 恢复派发 outbox 同事务提交
                self.db.commit()
            except (ApprovalConflict, ApprovalExpired, ApprovalNotFound) as exc:
                self.db.rollback()
                self._deny(hook_ctx, "APPROVAL_REJECTED", str(exc), "approval")

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
            if dangerous_effect is not None:
                EffectLedger(self.db).mark(dangerous_effect, "failed")
            self.db.commit()
            logger.warning("tool %s failed", name, exc_info=True)
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if dangerous_effect is not None:
            EffectLedger(self.db).mark(
                dangerous_effect,
                "succeeded" if (isinstance(result, ToolResult) and result.ok) else "failed",
                result_ref=str((result.meta or {}).get("source_ref", "")) if isinstance(result, ToolResult) else None,
            )

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

    def _deny(self, hook_ctx: HookContext, code: str, message: str, reason: str) -> None:
        decision = HookDecision(action=HookAction.BLOCK, code=code, message=message)
        write_hook_audit(self.db, hook_ctx, decision, action="hook.tool.block", detail={"reason": reason})
        self.db.commit()
        raise ToolDeniedError(message, code=code)

    async def _check_rate_limit(self, tool_name: str, rule: str | None, ctx: ToolCtx) -> tuple[bool, int]:
        limit, window = parse_rate_limit(rule) or DEFAULT_RATE_LIMIT
        actor_id = ctx.actor.user_id if ctx.actor else "anonymous"
        window_index = int(time.time()) // window
        key = ckey("tool", "rl", tool_name, actor_id, str(window_index))
        # 原子计数（Redis 脚本/内存锁内读改写）：并发下不超配额；内存模式仅单进程有效
        current = await cache_incr(key, ttl=window + 1)
        if current > limit:
            return False, window - int(time.time()) % window
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
