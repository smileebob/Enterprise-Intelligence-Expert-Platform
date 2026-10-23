"""化学六维打分器（D1–D6）—— 逐条移植《附-六维评分参考实现.py》与 08 文档 §七公式。

约定：每个打分器输入为 assembler 产出的结构化输入字典，输出
`{"score": float, "evidence_refs": [...], "deductions": [...], "detail": {...}}`。
公式数学上属于领域专有（决策 g/D7），以插件代码随领域走；底座提供注册表/标准层/校准/护栏流程。
"""

from __future__ import annotations

from typing import Any

from domains.chemical.evaluation.config import (
    COMPLIANCE_BASE,
    COMPLIANCE_RESTRICTED_CAP,
    COST_BIN_FLOOR,
    COST_BINS,
    COST_COEF,
)


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def d1_performance(inputs: dict[str, Any]) -> dict[str, Any]:
    """D1 性能达成度：Σ(attain × ev × 100) / n；attain 上限 1.1（超额小幅余量）。"""
    reqs = inputs.get("reqs") or []
    if not reqs:
        return {"score": 0.0, "evidence_refs": [], "deductions": [], "detail": {"reason": "无目标性能项"}}
    total = 0.0
    refs: list[str] = []
    detail_items = []
    for item in reqs:
        name = item.get("name", "")
        actual = float(item.get("actual", 0))
        target = float(item.get("target", 0))
        higher_better = bool(item.get("higher_better", True))
        ev = float(item.get("evidence", 1.0))
        ratio = (actual / target) if higher_better else (target / actual if actual else 0)
        attain = min(min(ratio, 1.0) + 0.1 * max(ratio - 1, 0), 1.1)
        total += attain * ev * 100
        refs.extend(item.get("refs", []))
        detail_items.append({"name": name, "actual": actual, "target": target, "attain": round(attain, 3), "evidence": ev})
    score = clamp(total / len(reqs))
    return {
        "score": round(score, 1),
        "evidence_refs": sorted(set(refs)),
        "deductions": [],
        "detail": {"items": detail_items},
    }


def d2_process(inputs: dict[str, Any]) -> dict[str, Any]:
    """D2 工艺适配性：100 起扣（新增工序/设备 −20×n 上限 −40；配套助剂 −10；窗口窄 −15/中 −8；一浴未验证 −10；common_issues −10×n 上限 −20）。"""
    deductions = [float(x.get("points", x) if isinstance(x, dict) else x) for x in inputs.get("deductions", [])]
    reasons = [x.get("reason", "") if isinstance(x, dict) else str(x) for x in inputs.get("deductions", [])]
    score = clamp(100 - sum(deductions))
    return {
        "score": round(score, 1),
        "evidence_refs": inputs.get("evidence_refs", []),
        "deductions": [{"points": p, "reason": r} for p, r in zip(deductions, reasons, strict=False)],
        "detail": {"total_deduction": sum(deductions)},
    }


def d3_fabric(inputs: dict[str, Any]) -> dict[str, Any]:
    """D3 面料适配性与风险：base(in90/partial70/out30) − Σ(风险扣分×规避系数0.5)。"""
    fabric_match = inputs.get("fabric_match", "in")
    base = {"in": 90, "partial": 70, "out": 30}.get(fabric_match, 30)
    risks = inputs.get("risks", [])
    deduction = 0.0
    risk_flags = []
    for risk in risks:
        points = float(risk.get("points", risk[0] if isinstance(risk, (list, tuple)) else 0))
        mitigated = bool(risk.get("mitigated", False))
        reason = risk.get("reason", "") if isinstance(risk, dict) else ""
        deduction += points * (0.5 if mitigated else 1.0)
        risk_flags.append({"reason": reason or "面料风险", "points": points, "mitigated": mitigated})
    score = clamp(base - deduction)
    return {
        "score": round(score, 1),
        "evidence_refs": inputs.get("evidence_refs", []),
        "deductions": [r for r in risk_flags if not r["mitigated"]],
        "detail": {"fabric_match": fabric_match, "base": base, "risks": risk_flags},
    }


def d4_validation(inputs: dict[str, Any]) -> dict[str, Any]:
    """D4 历史验证充分度：n=0 → 25（强制标注）；否则 clamp(100×min(n/3,1)×conf + (5 if cmp))；n<2 标注参考级。"""
    cases = inputs.get("cases", [])
    n = int(inputs.get("n_cases", len(cases)))
    if n == 0:
        return {
            "score": 25.0,
            "evidence_refs": [],
            "deductions": [{"points": 75, "reason": "无内部验证，仅理论"}],
            "detail": {"n_cases": 0, "guardrail": "无内部验证，仅理论", "n_lt2": True},
        }
    avg_conf = float(inputs.get("avg_conf", sum(c.get("confidence", 0.8) for c in cases) / max(len(cases), 1)))
    has_compare = bool(inputs.get("has_compare", any(c.get("has_compare") for c in cases)))
    score = clamp(100 * min(n / 3, 1) * avg_conf + (5 if has_compare else 0))
    return {
        "score": round(score, 1),
        "evidence_refs": [c.get("case_id", "") for c in cases if c.get("case_id")],
        "deductions": [],
        "detail": {"n_cases": n, "avg_conf": round(avg_conf, 3), "has_compare": has_compare, "n_lt2": n < 2},
    }


def d5_cost(inputs: dict[str, Any]) -> dict[str, Any]:
    """D5 成本与经济性：C=Σ(用量×系数)/100 → 六档分位 − 5×额外工序。"""
    materials = inputs.get("materials", [])
    extra_steps = int(inputs.get("extra_steps", 0))
    cost_index = sum(COST_COEF.get(str(material), 2.0) * float(amount) for material, amount in materials) / 100
    base = COST_BIN_FLOOR
    for threshold, bin_score in COST_BINS:
        if cost_index <= threshold:
            base = bin_score
            break
    score = clamp(base - 5 * extra_steps)
    return {
        "score": round(score, 1),
        "evidence_refs": inputs.get("evidence_refs", []),
        "deductions": ([{"points": 5 * extra_steps, "reason": f"额外工序 {extra_steps} 道"}] if extra_steps else []),
        "detail": {"cost_index": round(cost_index, 2), "base": base, "extra_steps": extra_steps},
    }


def d6_compliance(inputs: dict[str, Any]) -> dict[str, Any]:
    """D6 合规与可持续性：clean100/ok80/restricted_substance70；市场命中限制 → min(base,30)（CS-05 确定性反转）。"""
    levels = [p.get("compliance_level", "ok") for p in inputs.get("products", [])]
    base = min((COMPLIANCE_BASE.get(level, 80) for level in levels), default=80)
    market = inputs.get("market", "unknown")
    restricted_products = [p.get("product_id", "") for p in inputs.get("products", []) if p.get("market_restricted")]
    triggered = bool(restricted_products) and market == "eu"
    if triggered:
        base = min(base, COMPLIANCE_RESTRICTED_CAP)  # CS-05：合规例外以外部法规为准
    compliance_flags = []
    if triggered:
        compliance_flags.append(
            {
                "rule_id": "CS-05-PFAS-EU",
                "level": "restricted",
                "products": restricted_products,
                "notice": "目标市场法规命中限制（PFAS/REACH），以外部法规为准，建议无氟替代",
            }
        )
    return {
        "score": round(clamp(base), 1),
        "evidence_refs": inputs.get("evidence_refs", []),
        "deductions": ([{"points": 100 - base, "reason": "命中法规限制（C6/PFAS）"}] if triggered else []),
        "detail": {
            "levels": levels,
            "market": market,
            "triggered": triggered,
            "hard_cap": COMPLIANCE_RESTRICTED_CAP if triggered else None,
        },
        "compliance_flags": compliance_flags,
    }
