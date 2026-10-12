"""化学领域生成器：结构化草稿组装（规则通道；live 模式可叠加大模型润色）。

每条结论强制挂 citation_id（BR-02）；合规例外（CS-05）时反转推荐并强制提示；
冲突由融合层传入，渲染层统一输出双方观点与裁决说明。
"""

from __future__ import annotations

from typing import Any

from core.abstraction.dtos import Evidence, FusedEvidence
from core.orchestration.state import Claim, Draft, DraftSection, Need

DOMAIN = "chemical"

CATEGORY_BY_PERFORMANCE: dict[str, str] = {
    "防水": "防水剂",
    "防油": "防水剂",
    "无氟": "防水剂",
    "有机氟": "防水剂",
    "柔软": "柔软剂",
    "抗静电": "功能整理剂",
    "增深": "功能整理剂",
    "吸湿排汗": "功能整理剂",
    "固色": "固色剂",
}

DIAGNOSTIC_MARKERS = ("泛黄", "原因", "为什么", "怎么解决", "反馈", "投诉")


def _evidence_by_type(fused: FusedEvidence) -> dict[str, list[Evidence]]:
    grouped: dict[str, list[Evidence]] = {}
    for item in fused.evidence:
        record_type = str((item.meta or {}).get("record_type", ""))
        grouped.setdefault(record_type, []).append(item)
    return grouped


def _cids_for(grouped: dict[str, list[Evidence]], types: list[str], record_ids: set[str] | None = None) -> list[str]:
    result: list[str] = []
    for record_type in types:
        for item in grouped.get(record_type, []):
            if record_ids is not None and str((item.meta or {}).get("record_id", "")) not in record_ids:
                continue
            if item.citation_id and item.citation_id not in result:
                result.append(item.citation_id)
    return result


def _record_id(item: Evidence) -> str:
    return str((item.meta or {}).get("record_id", ""))


def _find(grouped: dict[str, list[Evidence]], record_type: str, record_id: str) -> Evidence | None:
    for item in grouped.get(record_type, []):
        if _record_id(item) == record_id:
            return item
    return None


def _fmt_perf(fields: dict[str, Any]) -> str:
    perf = fields.get("performance") or {}
    parts = [f"{k} {v}" for k, v in list(perf.items())[:3]]
    return "、".join(parts)


def _fmt_composition(fields: dict[str, Any]) -> str:
    composition = fields.get("composition") or []
    parts = []
    for comp in composition[:5]:
        amount = comp.get("amount_g_per_L", "")
        role = comp.get("role", "")
        parts.append(f"{comp.get('material', '')}{(' ' + str(amount)) if amount else ''}{('（' + role + '）') if role else ''}")
    return "；".join(parts)


def _select_products(need: Need, grouped: dict[str, list[Evidence]]) -> list[Evidence]:
    products = grouped.get("products", [])
    wanted_categories = {CATEGORY_BY_PERFORMANCE[perf] for perf in need.target_performance if perf in CATEGORY_BY_PERFORMANCE}
    if wanted_categories:
        matched = [p for p in products if str((p.meta or {}).get("fields", {}).get("category", "")) in wanted_categories]
        matched_top = [x for x in matched][:3]
        # 用户点名产品优先
        named = [p for p in products if _record_id(p) in need.product_ids]
        for item in named:
            if item not in matched_top:
                matched_top.insert(0, item)
        if matched_top:
            return matched_top[:3]
    return products[:2]


def compose(
    need: Need,
    fused: FusedEvidence,
    eval_result: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    degrade_level: str = "L0",
) -> Draft:
    grouped = _evidence_by_type(fused)
    draft = Draft(
        conflicts=list(fused.conflicts),
        eval_result=eval_result,
        override_flags=list(fused.override_flags),
        degrade_level=degrade_level,
        scored_by=(eval_result or {}).get("scored_by", ""),
    )
    is_diagnostic = any(marker in need.raw_text for marker in DIAGNOSTIC_MARKERS)

    sections: list[DraftSection] = []

    if is_diagnostic:
        sections.extend(_diagnostic_sections(need, grouped))
    else:
        sections.append(_recommendation_section(need, grouped, fused))
        basis = _basis_section(grouped, need)
        if basis.claims:
            sections.append(basis)

    validation = _validation_section(grouped, need)
    if validation.claims:
        sections.append(validation)

    draft.sections = [section for section in sections if section.claims]
    draft.summary = _summary(need, grouped, fused)
    draft.citations = [
        {
            "citation_id": e.citation_id,
            "chunk_id": e.chunk_id,
            "record_id": _record_id(e),
            "source_type": e.source_type,
            "source_ref": e.source_ref,
            "confidence": e.confidence,
            "category": e.category,
        }
        for e in fused.evidence
        if e.citation_id
    ]
    draft.confidence = _confidence(need, grouped, fused)
    return draft


def _summary(need: Need, grouped: dict[str, list[Evidence]], fused: FusedEvidence) -> str:
    products = _select_products(need, grouped)
    names = [str((p.meta or {}).get("fields", {}).get("name") or _record_id(p)) for p in products]
    parts = [f"针对「{need.raw_text[:40]}」的需求："]
    if names:
        parts.append(f"推荐 {'、'.join(names[:2])}，详见下方方案（每条结论附来源引用）。")
    else:
        parts.append("基于检索到的内部与外部证据给出分析（每条结论附来源引用）。")
    if fused.override_flags:
        parts.append("⚠ 本单命中合规强制提示，请优先阅读「合规强制提示」。")
    if fused.conflicts:
        parts.append("⚠ 检出内外部知识冲突，已按内部优先裁决并保留双方观点。")
    return "".join(parts)


def _recommendation_section(need: Need, grouped: dict[str, list[Evidence]], fused: FusedEvidence) -> DraftSection:
    section = DraftSection(title="推荐方案")

    # CS-05 合规例外：确定性规则已裁决 → 明确替代建议（引用外部法规 + 替代产品）
    for flag in fused.override_flags:
        restricted = list(flag.get("restricted_products") or [])
        recommend = list(flag.get("recommend_products") or [])
        if not restricted:
            continue
        cite: list[str] = _cids_for(grouped, ["external_knowledge"], {"EXT-STD-006"}) or _cids_for(grouped, ["external_knowledge"])
        for rid in recommend:
            cite.extend(_cids_for(grouped, ["products"], {rid}))
        claim_text = (
            f"合规优先（出口受限市场）：不建议使用 {'、'.join(restricted)}（命中法规限制）；"
            f"建议改用无氟（C0）替代 {'、'.join(recommend) if recommend else '（参见合规提示）'} + 交联剂配套"
        )
        if cite:
            section.claims.append(Claim(text=claim_text, citation_ids=sorted(set(cite))))

    products = _select_products(need, grouped)
    for product in products:
        fields = (product.meta or {}).get("fields", {})
        rid = _record_id(product)
        perf = _fmt_perf(fields)
        text = f"产品：{product.content.split('：', 1)[1][:120] if '：' in product.content else rid}"
        if perf:
            text = f"推荐产品 {rid}：{perf}"
        section.claims.append(Claim(text=text, citation_ids=[product.citation_id]))

        # 配方（敏感字段后续由输出 Hook 按角色脱敏）
        formula = None
        for candidate in grouped.get("formulas", []):
            if str((candidate.meta or {}).get("fields", {}).get("product_id", "")) == rid:
                formula = candidate
                break
        if formula is not None:
            ffields = (formula.meta or {}).get("fields", {})
            comp = _fmt_composition(ffields)[:180]
            cure = ffields.get("cure", "")
            section.claims.append(
                Claim(
                    text=f"工作液配方 {_record_id(formula)}：{comp}{('；焙烘：' + str(cure)) if cure else ''}",
                    citation_ids=[formula.citation_id],
                )
            )

        # 工艺
        process_ref = str(fields.get("process_ref", ""))
        process = _find(grouped, "processes", process_ref) if process_ref else None
        if process is None and grouped.get("processes"):
            process = grouped["processes"][0]
        if process is not None:
            pfields = (process.meta or {}).get("fields", {})
            conditions = pfields.get("conditions") or {}
            cond_text = "；".join(f"{k} {v}" for k, v in list(conditions.items())[:4])
            section.claims.append(
                Claim(text=f"工艺 {_record_id(process)}：{cond_text}", citation_ids=[process.citation_id])
            )
    return section


def _basis_section(grouped: dict[str, list[Evidence]], need: Need) -> DraftSection:
    section = DraftSection(title="关键依据")
    experiments = grouped.get("experiment_cases", [])[:3]
    for exp in experiments:
        fields = (exp.meta or {}).get("fields", {})
        conclusion = str(fields.get("conclusion", ""))[:120]
        section.claims.append(Claim(text=f"{_record_id(exp)}：{conclusion}", citation_ids=[exp.citation_id]))
    return section


def _diagnostic_sections(need: Need, grouped: dict[str, list[Evidence]]) -> list[DraftSection]:
    cause = DraftSection(title="原因分析")
    solve = DraftSection(title="解决方案")
    for item in grouped.get("external_knowledge", []):
        claims = (item.meta or {}).get("fields", {}).get("key_claims") or []
        mechanism = next((c for c in claims if any(k in c for k in ("机理", "氧化", "发色", "高温"))), None)
        if mechanism:
            cause.claims.append(Claim(text=f"机理（外部文献）：{mechanism}", citation_ids=[item.citation_id]))
    for item in grouped.get("experiment_cases", []):
        if (item.meta or {}).get("conflict_ref") == "CS-02" or "黄变" in item.content:
            fields = (item.meta or {}).get("fields", {})
            conclusion = str(fields.get("conclusion", ""))[:140]
            cause.claims.append(Claim(text=f"内部实测：{conclusion}", citation_ids=[item.citation_id]))
            solve.claims.append(
                Claim(
                    text="解决方向：控制焙烘温度≤170℃、降低氨基硅油用量，或改用低黄变/亲水型号（如 TF-SOFT-3305）；具体可按内部实验结论执行",
                    citation_ids=[item.citation_id],
                )
            )
    for item in grouped.get("products", []):
        if _record_id(item) == "TF-SOFT-3305":
            solve.claims.append(Claim(text="替代型号：TF-SOFT-3305（亲水氨基硅油，兼顾柔软与亲水，适合贴身面料）", citation_ids=[item.citation_id]))
    return [cause, solve]


def _validation_section(grouped: dict[str, list[Evidence]], need: Need) -> DraftSection:
    section = DraftSection(title="后续验证方向")
    refs: list[str] = []
    for record_type in ("processes", "formulas"):
        for item in grouped.get(record_type, [])[:1]:
            if item.citation_id:
                refs.append(item.citation_id)
    if refs:
        section.claims.append(
            Claim(
                text="建议按客户实际设备条件做小样复现，重点验证焙烘充分度与交联剂用量（达成耐洗/手感的关键条件）",
                citation_ids=refs,
            )
        )
    return section


def _confidence(need: Need, grouped: dict[str, list[Evidence]], fused: FusedEvidence) -> float:
    # 08 §七：方案置信度 = 支撑案例平均conf×0.5 + 数据完整度×0.3 + 冲突消解确定性×0.2
    cases = grouped.get("experiment_cases", [])
    avg_conf = sum((c.confidence or 0.8) for c in cases) / len(cases) if cases else 0.7
    filled = sum(1 for value in (need.slots or {}).values() if value)
    data_completeness = min(1.0, 0.4 + 0.12 * filled)
    if not fused.conflicts:
        certainty = 1.0
    else:
        certainty = 0.7  # 内部实测明确裁决 / 多源加权
    return round(avg_conf * 0.5 + data_completeness * 0.3 + certainty * 0.2, 3)
