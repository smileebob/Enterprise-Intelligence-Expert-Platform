"""招商六维打分器（E1–E6；轻量装配）。

维度与领域权重依据产品 02 §4.2（技术先进性/资本健康度/团队实力/市场与商业化/成长动能/落地匹配度，
权重按领域配置于 domain.yaml scenario_profiles）。
数据充分度护栏：<0.6 标注「数据有限，仅供参考」，不出强结论。
"""

from __future__ import annotations

from typing import Any

DIM_CODES = ["E1", "E2", "E3", "E4", "E5", "E6"]


def _dim_score(inputs: dict[str, Any], code: str, name: str) -> dict[str, Any]:
    six_dim = inputs.get("six_dim") or {}
    score = float(six_dim.get(code, 0))
    completeness = float(inputs.get("data_completeness", 0))
    deductions = []
    if completeness < 0.6:
        deductions.append({"points": 0, "reason": "数据充分度不足（<0.6）：参考级，不做强结论"})
    return {
        "score": round(score, 1),
        "evidence_refs": inputs.get("evidence_refs", []),
        "deductions": deductions,
        "detail": {"dim": name, "data_completeness": completeness, "reference_level": completeness < 0.6},
    }


def e1_technology(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E1", "技术先进性")


def e2_capital(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E2", "资本健康度")


def e3_team(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E3", "团队实力")


def e4_market(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E4", "市场与商业化")


def e5_growth(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E5", "成长动能")


def e6_fit(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "E6", "落地匹配度")
