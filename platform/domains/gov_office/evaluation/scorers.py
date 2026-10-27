"""政务办公六维打分器（G1–G6）。

维度分来自后续领域评估装配产出的结构化检查结果；缺失数据记 0 并注明原因，
不根据文本长度或模型自评伪造质量分。
"""

from __future__ import annotations

from typing import Any


def _dim_score(inputs: dict[str, Any], code: str, name: str) -> dict[str, Any]:
    scores = inputs.get("dimension_scores") or {}
    raw = scores.get(code)
    deductions: list[dict[str, Any]] = []
    if raw is None:
        score = 0.0
        deductions.append({"points": 0, "reason": f"{name}缺少可计算检查结果"})
    else:
        score = max(0.0, min(100.0, float(raw)))
    return {
        "score": round(score, 1),
        "evidence_refs": list(inputs.get("evidence_refs") or []),
        "deductions": deductions,
        "detail": {
            "dim": name,
            "data_completeness": float(inputs.get("data_completeness", 0.0)),
            "support_status": inputs.get("support_status", "unknown"),
        },
    }


def g1_policy_fit(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G1", "政策适用性")


def g2_evidence_support(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G2", "证据支持度")


def g3_task_completeness(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G3", "任务完整性")


def g4_document_norm(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G4", "公文规范性")


def g5_compliance(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G5", "合规与安全")


def g6_usability(inputs: dict[str, Any]) -> dict[str, Any]:
    return _dim_score(inputs, "G6", "表达可用性")
