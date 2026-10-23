"""① 输入 Hook：注入检测（规则集）+ PII 脱敏 + 越权预检（技术方案 03 §3.6）。

织入点：FastAPI 路由依赖层（chat 消息入口）；产出脱敏 payload 进图。
规则通道确定性优先；live 模式下可再叠加 LLM 判别（此处保留接口位）。
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from core.abstraction.enums import HookAction
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit
from core.guardrails.masking import detect_pii, mask_pii

# 注入检测规则集（演示场景的确定性防线）
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(ignore\s+(all\s+)?(previous|above)\s+instructions?)", "忽略指令注入"),
    (r"(忽略|无视|绕过)(以上|上面|之前|前面)?(的)?(所有)?(指令|规则|约束|提示词)", "忽略指令注入"),
    (r"(?i)(system\s*prompt|系统提示词|开发者模式|developer\s*mode)", "提示词探测"),
    (r"(?i)(drop\s+table|delete\s+from|truncate\s+table)", "SQL 注入"),
    (r"<script[^>]*>", "XSS 脚本注入"),
    (r"(?i)(rm\s+-rf|format\s+c:)", "危险命令注入"),
]


def detect_injection(text: str) -> list[str]:
    hits: list[str] = []
    for pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, text or ""):
            hits.append(label)
    return hits


class InputGuard:
    """输入 Hook：注入检测 + PII 脱敏 + 审计。"""

    def __init__(self, db: Session):
        self.db = db

    def check(self, text: str, ctx: HookContext) -> HookDecision:
        hits = detect_injection(text)
        if hits:
            decision = HookDecision(
                action=HookAction.BLOCK,
                code="INJECTION_DETECTED",
                message=f"输入包含不安全内容（{('、'.join(hits))}），已拦截",
                extra={"hits": hits},
            )
            write_hook_audit(self.db, ctx, decision, action="hook.input.block", detail={"hits": hits})
            self.db.commit()
            return decision

        pii = detect_pii(text)
        masked = mask_pii(text)
        if pii:
            decision = HookDecision(
                action=HookAction.MASK,
                payload={"text": masked},
                message=f"个人敏感信息已脱敏（{('、'.join(pii))}）",
                extra={"pii": pii},
            )
        else:
            decision = HookDecision(action=HookAction.PASS, payload={"text": text})
        write_hook_audit(
            self.db,
            ctx,
            decision,
            action="hook.input." + ("mask" if pii else "pass"),
            detail={"pii": pii, "length": len(text or "")},
        )
        self.db.commit()
        return decision
