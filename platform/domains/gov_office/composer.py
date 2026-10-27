"""政务办公草稿生成：只基于检索证据组装可追溯草稿，不补写事实。

当前为机制切片：政策条款陈述 + 明确的不确定边界。正式政策解读、公文模板和审校
仍由后续领域节点与业务服务实现，不能把此规则通道当成真实大模型质量。
"""

from __future__ import annotations

from typing import Any

from core.abstraction.dtos import Evidence, FusedEvidence
from core.orchestration.state import Claim, Draft, DraftSection, Need


def _policy_clause(item: Evidence) -> bool:
    return str((item.meta or {}).get("record_type", "")) == "policy_clause"


def compose(
    need: Need,
    fused: FusedEvidence,
    eval_result: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    degrade_level: str = "L0",
) -> Draft:
    clauses = [item for item in fused.evidence if _policy_clause(item) and item.citation_id]
    draft = Draft(
        conflicts=list(fused.conflicts),
        eval_result=eval_result,
        override_flags=list(fused.override_flags),
        degrade_level=degrade_level,
    )

    basis = DraftSection(title="适用依据")
    for item in clauses[:5]:
        text = item.content.strip()
        if text:
            basis.claims.append(Claim(text=text, citation_ids=[item.citation_id]))

    boundary = DraftSection(title="结论边界")
    if clauses:
        boundary.claims.append(
            Claim(
                text="上述内容仅为政策条款摘录。具体适用仍需结合地区、对象、事项时间及完整条款核对。",
                citation_ids=[clauses[0].citation_id],
            )
        )
    else:
        draft.annotations.append("未检索到已审核政策条款，不形成确定性政策结论。")

    if len(fused.conflicts):
        draft.annotations.append(f"存在 {len(fused.conflicts)} 项待核冲突，需人工确认。")

    draft.sections = [section for section in (basis, boundary) if section.claims]
    draft.summary = (
        f"围绕「{need.raw_text[:60]}」检索到 {len(clauses)} 条已审核政策条款，"
        "以下只陈述条款内容，不代替主管部门的个案判断。"
        if clauses
        else f"围绕「{need.raw_text[:60]}」未找到可支撑结论的已审核条款。"
    )
    draft.citations = [
        {
            "citation_id": item.citation_id,
            "chunk_id": item.chunk_id,
            "record_id": (item.meta or {}).get("record_id", ""),
            "source_type": item.source_type,
            "source_ref": item.source_ref,
            "confidence": item.confidence,
        }
        for item in clauses
    ]
    draft.confidence = 0.7 if clauses else 0.0
    return draft
