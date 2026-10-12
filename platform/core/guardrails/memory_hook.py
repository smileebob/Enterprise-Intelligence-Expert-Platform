"""⑤ 记忆写入 Hook：置信度门槛 + 防污染 + 字段完整性（03 §3.6 / BR-06）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.abstraction.dtos import MemoryCandidate
from core.abstraction.enums import HookAction, MemoryLayer
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit

MIN_CONFIDENCE = 0.5
PROMOTION_LAYERS = {MemoryLayer.ENTERPRISE, MemoryLayer.DOMAIN}


class MemoryWriteGuard:
    def __init__(self, db: Session):
        self.db = db

    def check(self, candidate: MemoryCandidate, ctx: HookContext) -> HookDecision:
        problems: list[str] = []
        if not (candidate.content or "").strip():
            problems.append("内容为空")
        if candidate.layer not in {layer.value for layer in MemoryLayer}:
            problems.append(f"未知记忆层: {candidate.layer}")
        if candidate.confidence < MIN_CONFIDENCE:
            problems.append(f"置信度 {candidate.confidence:.2f} 低于写入门槛 {MIN_CONFIDENCE}")
        # 防污染（BR-06）：晋升到企业/领域层必须有晋升依据（多人验证/人工审核）
        if candidate.layer in {layer.value for layer in PROMOTION_LAYERS} and not candidate.meta.get("promotion_basis"):
            problems.append("晋升企业级/领域级记忆缺少晋升依据（多人验证或管理员审核）")

        if problems:
            decision = HookDecision(
                action=HookAction.BLOCK,
                code="STATE_INVALID",
                message="记忆写入被拦截：" + "；".join(problems),
                extra={"problems": problems},
            )
        else:
            decision = HookDecision(action=HookAction.PASS, payload={"accepted": True})

        write_hook_audit(
            self.db,
            ctx,
            decision,
            action="hook.memory_write." + str(decision.action),
            detail={"problems": problems, "layer": candidate.layer, "origin": candidate.origin},
        )
        return decision
