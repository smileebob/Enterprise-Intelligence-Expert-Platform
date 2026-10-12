"""招商领域生成器：企业画像 + 园区资源匹配 + 招商切入策略（轻量规则通道）。"""

from __future__ import annotations

from typing import Any

from core.abstraction.dtos import Evidence, FusedEvidence
from core.orchestration.state import Claim, Draft, DraftSection, Need

DOMAIN = "investment"

DIM_NAMES = {"E1": "技术先进性", "E2": "资本健康度", "E3": "团队实力", "E4": "市场与商业化", "E5": "成长动能", "E6": "落地匹配度"}


def _record_id(item: Evidence) -> str:
    return str((item.meta or {}).get("record_id", ""))


def compose(
    need: Need,
    fused: FusedEvidence,
    eval_result: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    degrade_level: str = "L0",
) -> Draft:
    draft = Draft(
        conflicts=list(fused.conflicts),
        eval_result=eval_result,
        override_flags=list(fused.override_flags),
        degrade_level=degrade_level,
    )
    enterprises = [e for e in fused.evidence if (e.meta or {}).get("record_type") == "enterprises"]
    resources = [e for e in fused.evidence if (e.meta or {}).get("record_type") == "park_resources"]
    policies = [e for e in fused.evidence if (e.meta or {}).get("record_type") == "policies"]

    portrait = DraftSection(title="高潜企业画像")
    for ent in enterprises[:4]:
        fields = (ent.meta or {}).get("fields", {})
        six_dim = fields.get("six_dim") or {}
        completeness = float(fields.get("completeness", 0))
        dim_text = "、".join(f"{DIM_NAMES.get(k, k)} {v}" for k, v in six_dim.items())
        guard = "（数据有限，仅供参考）" if completeness < 0.6 else ""
        portrait.claims.append(
            Claim(text=f"{ent.content.split('：', 1)[0]}：{dim_text}{guard}", citation_ids=[ent.citation_id])
        )

    match = DraftSection(title="园区资源匹配")
    for res in resources[:3]:
        match.claims.append(Claim(text=res.content.split("。", 1)[0], citation_ids=[res.citation_id]))
    for pol in policies[:2]:
        match.claims.append(Claim(text=pol.content.split("。", 1)[0], citation_ids=[pol.citation_id]))

    strategy = DraftSection(title="招商切入策略")
    refs = [e.citation_id for e in enterprises[:1] + resources[:1] if e.citation_id]
    if refs:
        strategy.claims.append(
            Claim(
                text="建议切入：围绕企业研发/选址/政策需求匹配园区空间与政策资源，优先安排实地考察与政策测算",
                citation_ids=refs,
            )
        )

    draft.sections = [s for s in (portrait, match, strategy) if s.claims]
    names = [e.content.split("（", 1)[0][:24] for e in enterprises[:2]]
    draft.summary = f"针对「{need.raw_text[:40]}」：筛选出 {len(enterprises)} 家候选企业" + (f"（{'、'.join(names)} 等）" if names else "") + "，画像与资源匹配如下（每条结论附来源引用）。"
    draft.citations = [
        {
            "citation_id": e.citation_id,
            "chunk_id": e.chunk_id,
            "record_id": _record_id(e),
            "source_type": e.source_type,
            "source_ref": e.source_ref,
            "confidence": e.confidence,
        }
        for e in fused.evidence
        if e.citation_id
    ]
    draft.confidence = 0.75
    return draft
