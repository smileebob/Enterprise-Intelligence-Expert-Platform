"""化学评估配置表（版本化；技术方案 08 风险 R3：成本系数/阈值全部进配置表）。

公式常量与档位表的唯一来源；打分器（scorers.py）从这里读取，禁止散落魔法值。
"""

from __future__ import annotations

CONFIG_VERSION = "2026Q4-chemical-v1"

# 成本系数表（模拟，真实单价待业务提供；08 §七）
COST_COEF: dict[str, float] = {
    "TF-WR-6612": 5.0,
    "TF-SS-2210": 4.0,
    "TF-WR-6608": 3.0,
    "TF-SOFT-2201": 3.0,
    "TF-SOFT-3305": 2.5,
    "TF-DP-7701": 2.5,
    "TF-WS-4401": 2.5,
    "TF-AS-8801": 2.0,
    "TF-FIX-9901": 2.0,
    "交联剂": 2.0,
    "TF-SOFT-5501": 1.5,
    "渗透剂JFC": 1.0,
}

# D5 成本指数分档（08 §七）
COST_BINS: list[tuple[float, int]] = [(1.0, 100), (1.5, 90), (2.0, 78), (2.5, 65), (3.0, 52)]
COST_BIN_FLOOR = 40

# D3 风险扣分表（08 §七）
FABRIC_RISK_POINTS = {"黄变": 20, "强力下降": 15, "色光变化": 10, "手感负面": 10}
FABRIC_RISK_MILD_POINTS = 15  # 轻微（如轻微黄变）
MITIGATION_FACTOR = 0.5

# D6 合规等级基准分（08 §七）
COMPLIANCE_BASE = {"clean": 100, "ok": 80, "restricted_substance": 70}
COMPLIANCE_RESTRICTED_CAP = 30  # CS-05：命中市场限制 → min(base, 30)

# D4 维度级护栏阈值（08 §七）
D4_REFERENCE_LEVEL_N = 2
