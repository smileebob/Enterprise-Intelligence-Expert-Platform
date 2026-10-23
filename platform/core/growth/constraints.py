"""结构化纠错规则的确定性应用（06-B）：仅执行已批准的结构化类型。"""

from __future__ import annotations

from typing import Any

from core.orchestration.state import Draft


def apply_constraint_rules(draft: Draft, rules: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    applied: list[str] = []
    effects: list[dict[str, Any]] = []
    for rule in rules:
        rule_type = str(rule.get("rule_type") or "text_only")
        rule_id = str(rule.get("rule_id") or "")
        if not rule_id:
            continue
        if rule_type == "forbid_claim":
            target = str(rule.get("target_fact") or "").strip()
            if not target:
                continue
            removed: list[str] = []
            for section in draft.sections:
                kept = []
                for claim in section.claims:
                    if target in claim.text:
                        removed.append(claim.text[:80])
                    else:
                        kept.append(claim)
                section.claims = kept
            if removed:
                applied.append(rule_id)
                effects.append({"rule_id": rule_id, "type": rule_type, "removed_claims": len(removed)})
                draft.annotations.append(
                    f"已应用纠错规则 {rule_id}：移除 {len(removed)} 条与「{target}」冲突的结论"
                )
        elif rule_type == "add_verification":
            text = str(rule.get("rule_text") or "").strip()
            if not text:
                continue
            applied.append(rule_id)
            effects.append({"rule_id": rule_id, "type": rule_type})
            draft.annotations.append(f"补充验证要求（{rule_id}）：{text[:160]}")
        # order_preference / text_only：仅供人工阅读，不自动执行
    draft.applied_constraint_ids = list(dict.fromkeys(applied))
    draft.constraint_effects = effects
    return draft.applied_constraint_ids, effects
