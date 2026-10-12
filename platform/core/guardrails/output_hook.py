"""④ 输出 Hook：引用存在性校验 / 对外脱敏 / 合规强制提示 / BR-10 低置信标注（03 §3.6）。

织入点：输出装配器（草稿定稿 → 对外渲染之间）；「先校验后外发」——正文 token 经本 Hook
通过后才允许开流（05 §5.2 决策 D11）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.abstraction.dtos import Evidence
from core.abstraction.enums import HookAction
from core.guardrails.hooks import HookContext, HookDecision, write_hook_audit
from core.guardrails.masking import mask_text_for_role
from core.orchestration.state import Draft

VERIFY_NOTE = "AI 推断，待核验"
MANUAL_REVIEW_NOTE = "需人工审核"


@dataclass
class OutputCheck:
    text: str
    annotations: list[str] = field(default_factory=list)
    masked_fields: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    unverified_claims: int = 0

    @classmethod
    def build(cls, text: str) -> OutputCheck:
        return cls(text=text)


def render_draft(draft: Draft) -> str:
    """结构化草稿 → 对外文本（每条结论挂引用角标）。"""
    lines: list[str] = []
    if draft.summary:
        lines.append(draft.summary)
        lines.append("")
    for section in draft.sections:
        lines.append(f"【{section.title}】")
        for claim in section.claims:
            suffix = "".join(f"[{cid}]" for cid in claim.citation_ids)
            lines.append(f"- {claim.text}{suffix}")
        lines.append("")
    return "\n".join(lines).strip()


def _render_conflicts(draft: Draft) -> list[str]:
    blocks: list[str] = []
    for conflict in draft.conflicts:
        block = [f"【冲突标注 · {conflict.conflict_id}】{conflict.title}"]
        block.append(f"- 内部结论：{conflict.internal_claim.statement}")
        block.append(f"- 外部观点：{conflict.external_claim.statement}")
        must_note = conflict.expected_resolution.get("must_note") or conflict.expected_resolution.get("primary", "")
        if must_note:
            block.append(f"- 裁决说明：{must_note}")
        if conflict.status != "resolved":
            if "合规" in f"{conflict.topic}{conflict.conflict_type}":
                # 合规例外（CS-05 类）：涉及法规/安全/合规时以权威外部标准为准，不得套用「内部优先」模板语
                block.append("- 处置：以权威外部法规为准（合规例外），已保留双方来源（存在冲突，建议人工核验）")
            else:
                block.append("- 处置：以内部为准，已保留双方来源（存在冲突，建议人工核验）")
        blocks.append("\n".join(block))
    return blocks


def _render_compliance(draft: Draft) -> list[str]:
    blocks: list[str] = []
    for flag in draft.override_flags:
        lines = [f"【合规强制提示 · {flag.get('rule_id', '')}】"]
        if flag.get("restricted_products"):
            lines.append(f"- 受限产品：{'、'.join(flag['restricted_products'])}（目标市场命中法规限制）")
        if flag.get("notice"):
            lines.append(f"- 说明：{flag['notice']}")
        if flag.get("recommend_products"):
            lines.append(f"- 推荐无氟替代：{'、'.join(flag['recommend_products'])}")
        blocks.append("\n".join(lines))
    return blocks


class OutputGuard:
    """输出 Hook 实现（引用校验 + 脱敏 + 合规提示 + 低置信标注）。"""

    def __init__(self, db: Session):
        self.db = db

    def check(
        self,
        draft: Draft,
        evidence: list[Evidence],
        ctx: HookContext,
        *,
        confidence_threshold: float = 0.6,
    ) -> OutputCheck:
        evidence_citation_ids = {e.citation_id for e in evidence if e.citation_id}
        evidence_record_ids = {str(e.meta.get("record_id", "")) for e in evidence}

        check = OutputCheck.build("")
        invalid: list[str] = []

        # 1) 引用存在性硬校验（BR-02）：无效引用剔除；无引用结论标注「AI 推断，待核验」
        for section in draft.sections:
            for claim in section.claims:
                valid_ids = [cid for cid in claim.citation_ids if cid in evidence_citation_ids]
                invalid.extend(set(claim.citation_ids) - set(valid_ids))
                claim.citation_ids = valid_ids
                if not valid_ids:
                    if not claim.text.endswith(VERIFY_NOTE):
                        claim.text = f"{claim.text}（{VERIFY_NOTE}）"
                    check.unverified_claims += 1
        check.invalid_citations = sorted(set(invalid))
        if check.invalid_citations:
            check.annotations.append(f"检出 {len(check.invalid_citations)} 条无效引用，已剔除")

        # 2) 冲突标注完备性（双方引用可见性）
        for conflict in draft.conflicts:
            refs = set(conflict.internal_claim.refs) | set(conflict.external_claim.refs)
            if not (refs & evidence_record_ids):
                check.annotations.append(f"冲突 {conflict.conflict_id} 未携带可引用证据，请人工核验")

        # 3) 合规强制提示（CS-05）
        compliance_blocks = _render_compliance(draft)

        # 4) 置信度门槛（BR-10）
        if draft.confidence and draft.confidence < confidence_threshold:
            check.annotations.append(
                f"{MANUAL_REVIEW_NOTE}（置信度 {draft.confidence:.2f} 低于领域阈值 {confidence_threshold:.2f}）"
            )

        # 5) 组装文本（正文 + 冲突 + 合规）
        parts: list[str] = [render_draft(draft)]
        parts.extend(_render_conflicts(draft))
        parts.extend(compliance_blocks)
        text = "\n\n".join(p for p in parts if p)

        # 6) 按角色动态脱敏（BR-08：三处口径一致）
        sensitive_fields: list[str] = []
        for e in evidence:
            sensitive_fields.extend((e.meta or {}).get("sensitive_fields", []))
        role_id = ctx.actor.role_id if ctx.actor else ""
        text, masked_fields = mask_text_for_role(text, role_id, sorted(set(sensitive_fields)))
        check.masked_fields = masked_fields
        check.text = text

        decision = HookDecision(
            action=HookAction.MASK if (masked_fields or check.invalid_citations) else HookAction.PASS,
            payload={"text": text, "annotations": check.annotations},
            extra={
                "invalid_citations": check.invalid_citations,
                "masked_fields": masked_fields,
                "unverified_claims": check.unverified_claims,
            },
        )
        write_hook_audit(
            self.db,
            ctx,
            decision,
            action="hook.output." + str(decision.action),
            detail={
                "invalid_citations": check.invalid_citations,
                "masked_fields": masked_fields,
                "unverified_claims": check.unverified_claims,
                "annotations": check.annotations,
                "length": len(text),
            },
        )
        return check

    def validate_citations_only(self, draft: Draft, evidence: list[Evidence]) -> dict[str, Any]:
        """自检节点用：仅做引用存在性/冲突标注机器校验（不落审计、不脱敏）。"""
        evidence_citation_ids = {e.citation_id for e in evidence if e.citation_id}
        unverified = 0
        for claim in draft.all_claims():
            if not claim.citation_ids or any(cid not in evidence_citation_ids for cid in claim.citation_ids):
                unverified += 1
        conflict_ok = True
        if draft.conflicts:
            record_ids = {str(e.meta.get("record_id", "")) for e in evidence}
            for conflict in draft.conflicts:
                refs = set(conflict.internal_claim.refs) | set(conflict.external_claim.refs)
                if not (refs & record_ids):
                    conflict_ok = False
        return {
            "unverified_claims": unverified,
            "conflict_noted": conflict_ok,
            "passed": unverified == 0 and conflict_ok,
        }
