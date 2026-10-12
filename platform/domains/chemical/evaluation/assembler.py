"""化学评估输入装配：object_ref → 六维打分器输入。

两条路径：
1. demo_case（三个金值算例）→ 读取 data/mock/eval_demo_cases.json 的对照输入（可逐项由 07 数据集复核）；
2. 通用路径 → 依据 chem_* 结构化表派生（目标性能/实验案例/配方材料/合规等级/面料适配）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from core.knowledge.compliance import detect_market, market_restricted
from domains.chemical.models import ChemExperiment, ChemFormula, ChemProcess, ChemProduct

_demo_cache: dict[str, dict[str, Any]] | None = None


def load_demo_cases() -> dict[str, dict[str, Any]]:
    global _demo_cache
    if _demo_cache is None:
        path = Path(get_settings().MOCK_DATA_DIR) / "eval_demo_cases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _demo_cache = {case["key"]: case for case in data.get("cases", [])}
    return _demo_cache


def build_scorer_inputs(db: Session, object_ref: dict[str, Any]) -> dict[str, Any]:
    demo_key = object_ref.get("demo_case")
    if demo_key:
        case = load_demo_cases().get(str(demo_key))
        if case:
            return dict(case["inputs"])
    return _derive_from_dataset(db, object_ref)


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_from_dataset(db: Session, object_ref: dict[str, Any]) -> dict[str, Any]:
    product_ids: list[str] = list(object_ref.get("products", []))
    products = [db.get(ChemProduct, pid) for pid in product_ids]
    products = [p for p in products if p is not None]
    formula = db.get(ChemFormula, object_ref.get("formula", "")) if object_ref.get("formula") else None
    process = db.get(ChemProcess, object_ref.get("process", "")) if object_ref.get("process") else None
    market = detect_market(str(object_ref.get("target_market", "")))

    inputs: dict[str, Any] = {"market": market}

    # ---- D1：目标性能（显式 performance_targets 优先；否则按标称达成） ----
    reqs: list[dict[str, Any]] = []
    for target in object_ref.get("performance_targets", []):
        reqs.append(
            {
                "name": target.get("metric", ""),
                "actual": float(target.get("actual", target.get("target", 0))),
                "target": float(target.get("target", 0)),
                "higher_better": bool(target.get("higher_better", True)),
                "evidence": float(target.get("evidence", 1.0)),
                "refs": target.get("refs", []),
            }
        )
    if not reqs:
        for product in products:
            for name, value in list((product.performance or {}).items())[:3]:
                number = _numeric(value)
                if number is None:
                    continue
                reqs.append(
                    {
                        "name": name,
                        "actual": number,
                        "target": number,
                        "higher_better": True,
                        "evidence": 0.85,  # 内部标称（无实测目标对拍）
                        "refs": [product.product_id],
                    }
                )
    inputs["reqs"] = reqs

    # ---- D2：工艺适配性（配套助剂/窗口） ----
    deductions: list[dict[str, Any]] = []
    if formula is not None:
        material_names = " ".join(str(c.get("material", "")) for c in (formula.composition or []))
        if "交联剂" in material_names:
            deductions.append({"points": 10, "reason": "需配套额外助剂（交联剂）"})
    for product in products:
        if "无氟" in (product.sub_type or ""):
            deductions.append({"points": 15, "reason": "无氟体系焙烘窗口窄（±5℃）"})
            break
        if "有机氟" in (product.sub_type or ""):
            deductions.append({"points": 8, "reason": "工艺窗口中（±10℃）"})
            break
    inputs["deductions"] = deductions
    inputs["evidence_refs"] = [ref for ref in (object_ref.get("process"), object_ref.get("formula")) if ref]

    # ---- D3：面料适配性 ----
    fabric = str(object_ref.get("fabric", ""))
    fabric_match = "in"
    if fabric and products:
        applicable = set()
        for product in products:
            applicable.update(product.applicable_fabric or [])
        if fabric in applicable:
            fabric_match = "in"
        elif any(fabric in item or item in fabric for item in applicable):
            fabric_match = "partial"
        else:
            fabric_match = "partial"
    risks: list[dict[str, Any]] = []
    cases = db.execute(
        select(ChemExperiment).where(ChemExperiment.product_id.in_([p.product_id for p in products] or [""]))
    ).scalars().all()
    yellowing = any("黄变" in str(case.results or {}) for case in cases)
    if yellowing and process is not None:
        bake = str((process.conditions or {}).get("焙烘", ""))
        mitigated = "160" in bake or "150" in bake  # 控温到位视为已规避
        risks.append({"points": 15, "reason": "轻微黄变（氨基硅油体系）", "mitigated": mitigated})
    inputs["fabric_match"] = fabric_match
    inputs["risks"] = risks
    inputs["fabric_evidence"] = [p.product_id for p in products]

    # ---- D4：历史验证 ----
    case_entries = []
    for case in cases:
        entry = {
            "case_id": case.case_id,
            "confidence": float(case.confidence) if case.confidence is not None else 0.85,
            "has_compare": "对比" in (case.title or "") or (case.process or "").startswith("对比"),
        }
        case_entries.append(entry)
    inputs["cases"] = case_entries
    inputs["n_cases"] = len(case_entries)
    inputs["avg_conf"] = round(sum(c["confidence"] for c in case_entries) / len(case_entries), 3) if case_entries else 0.0
    inputs["has_compare"] = any(c["has_compare"] for c in case_entries)

    # ---- D5：成本 ----
    materials: list[list[Any]] = []
    if formula is not None:
        for comp in formula.composition or []:
            amount = _numeric(comp.get("amount_g_per_L"))
            if amount is not None:
                materials.append([_cost_material_key(str(comp.get("material", ""))), amount])
    inputs["materials"] = materials
    inputs["extra_steps"] = 0

    # ---- D6：合规 ----
    product_entries = []
    for product in products:
        entry = {
            "product_id": product.product_id,
            "compliance_level": product.compliance_level,
            "sub_type": product.sub_type,
            "compliance": product.compliance,
            "category": product.category,
        }
        entry["market_restricted"] = market_restricted(
            market, {"sub_type": product.sub_type, "compliance": product.compliance, "compliance_level": product.compliance_level}
        )
        product_entries.append(entry)
    inputs["products"] = product_entries
    return inputs


def _cost_material_key(material: str) -> str:
    """配方原料名 → 成本系数表键（如 'TF-WR-6608 无氟防水剂' → 'TF-WR-6608'）。"""
    material = material.strip()
    if material.startswith("TF-"):
        return material.split()[0]
    if "交联剂" in material:
        return "交联剂"
    if "渗透剂" in material:
        return "渗透剂JFC"
    return material.split()[0]
