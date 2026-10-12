"""合规例外（CS-05）确定性规则表 —— 平台级不变量（附录A 决策 D9 采纳并强化）。

法规/安全/合规类证据的优先级反转由**确定性规则**执行，任何版本不得将其下沉给大模型裁决。
规则命中：目标市场命中受限法规（如出口欧盟 PFAS/REACH）且产品含受限物质（如 C6 有机氟）→
优先级整体反转（合规类外部标准 > 内部声明）、强制风险提示、推荐无氟替代。
"""

from __future__ import annotations

from dataclasses import dataclass, field

EU_MARKET_MARKERS = ("欧盟", "出口欧盟", "欧洲", "eu", "europe")
DOMESTIC_MARKERS = ("内销", "国内")
RESTRICTED_SUBSTANCE_MARKERS = ("c6", "有机氟", "restricted_substance", "pfas", "全氟")
NO_FLUORINE_MARKERS = ("无氟", "c0")

# 法规限制表（模拟）：目标市场 → 受限物质 → 说明
REGULATION_TABLE: list[dict] = [
    {
        "rule_id": "CS-05-PFAS-EU",
        "market": "eu",
        "restricted_markers": ["c6", "有机氟", "restricted_substance", "pfas", "全氟"],
        "notice": (
            "内部『环保』为营销口径，不能凌驾于 ZDHC/REACH 等法规之上；"
            "出口欧盟订单应评估 PFAS 限制风险，优先推荐无氟（C0）替代方案。"
        ),
    }
]


@dataclass
class ComplianceOverride:
    triggered: bool = False
    rule_id: str = ""
    market: str = "unknown"
    restricted_products: list[str] = field(default_factory=list)
    notice: str = ""
    recommend_products: list[str] = field(default_factory=list)


def detect_market(*texts: str | None) -> str:
    """从文本/上下文中判定目标市场（eu / domestic / unknown）。"""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return "unknown"
    if any(marker in blob for marker in EU_MARKET_MARKERS):
        return "eu"
    if any(marker in blob for marker in DOMESTIC_MARKERS):
        return "domestic"
    return "unknown"


def _is_restricted(product: dict) -> bool:
    blob = f"{product.get('sub_type', '')} {product.get('compliance', '')} {product.get('compliance_level', '')}".lower()
    return any(marker in blob for marker in RESTRICTED_SUBSTANCE_MARKERS)


def _is_no_fluorine(product: dict) -> bool:
    blob = f"{product.get('sub_type', '')} {product.get('compliance', '')}".lower()
    return any(marker in blob for marker in NO_FLUORINE_MARKERS)


def evaluate_compliance_override(market: str, products: list[dict]) -> ComplianceOverride:
    """确定性合规例外裁决（CS-05）：出口受限市场 + 产品含受限物质 → 反转 + 提示 + 替代推荐。

    products: [{product_id, category, sub_type, compliance, compliance_level}]
    """
    override = ComplianceOverride(market=market)
    if market != "eu":
        return override  # 内销/未知市场不强制合规拦截（CS-05 负向场景）

    restricted = [p["product_id"] for p in products if _is_restricted(p)]
    if not restricted:
        return override

    rule = REGULATION_TABLE[0]
    override.triggered = True
    override.rule_id = rule["rule_id"]
    override.restricted_products = restricted
    override.notice = rule["notice"]
    # 推荐替代：同类防水剂中的无氟产品
    categories = {p.get("category", "") for p in products if p["product_id"] in restricted}
    for p in products:
        if p["product_id"] in restricted:
            continue
        if _is_no_fluorine(p) and (not categories or p.get("category", "") in categories or p.get("category", "") == "防水剂"):
            override.recommend_products.append(p["product_id"])
    return override


def market_restricted(market: str, product: dict) -> bool:
    """单产品维度：目标市场是否命中法规限制（D6 打分用）。"""
    if market != "eu":
        return False
    return _is_restricted(product)
