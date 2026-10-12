"""结构化需求提取（澄清槽位 / 市场 / 场景画像 / 产品点名；规则通道）。

live 模式可由大模型增强（补充槽位语义），离线规则通道保证确定性可测。
"""

from __future__ import annotations

import re

from core.domains import DomainPlugin
from core.knowledge.compliance import detect_market
from core.orchestration.state import Need

PRODUCT_ID_RE = re.compile(r"\b([A-Z]{2,4}-[A-Z]{2,5}-\d{3,4})\b")

DIAGNOSTIC_MARKERS = ("泛黄", "原因", "为什么", "怎么解决", "反馈", "投诉")
CHALLENGE_MARKERS = ("评测", "凭什么", "网上", "冲突", "对比", "盲评")
ANALYSIS_MARKERS = ("因素", "分析", "优化", "综合", "怎么提升", "如何提升")
PROCESS_MARKERS = ("焙烘", "温度", "多少度", "工艺参数", "多长时间", "工艺条件")

CRITICAL_BY_MODE: dict[str, list[str]] = {
    "selection": ["面料成分", "目标性能"],
    "diagnostic": ["面料成分"],
    "process": [],
    "analysis": [],
    "challenge": [],
}

COST_MARKERS = ("成本", "价格", "降本", "预算", "成本可控")
PREMIUM_MARKERS = ("高端", "品牌", "性能优先", "手感优先", "高端品牌")
DEVICE_MARKERS = ("设备受限", "快交期", "交期", "设备老旧")


def classify_mode(text: str) -> str:
    if any(marker in text for marker in DIAGNOSTIC_MARKERS):
        return "diagnostic"
    if any(marker in text for marker in CHALLENGE_MARKERS):
        return "challenge"
    if any(marker in text for marker in ANALYSIS_MARKERS):
        return "analysis"
    if any(marker in text for marker in PROCESS_MARKERS) and "推荐" not in text:
        return "process"
    return "selection"


def infer_scenario(market: str, text: str) -> str:
    if market == "eu":
        return "出口合规敏感"
    if any(marker in text for marker in COST_MARKERS):
        return "成本敏感"
    if any(marker in text for marker in PREMIUM_MARKERS):
        return "高端品牌性能优先"
    if any(marker in text for marker in DEVICE_MARKERS):
        return "设备受限快交期"
    return "默认"


def extract_need(text: str, plugin: DomainPlugin, domain_id: str) -> Need:
    need = Need(raw_text=text, domain_id=domain_id)
    slots: dict[str, str] = {}
    slot_dictionary: dict[str, list[str]] = plugin.agent_config.get("slot_dictionary", {})
    for slot, keywords in slot_dictionary.items():
        hits = [kw for kw in keywords if kw in text]
        if hits:
            slots[slot] = "、".join(dict.fromkeys(hits))

    need.slots = slots
    need.fabric = slots.get("面料成分", "")
    need.target_performance = [p for p in slots.get("目标性能", "").split("、") if p]
    need.market = detect_market(text, slots.get("目标市场"))
    need.scenario = infer_scenario(need.market, text)
    need.product_ids = sorted(set(PRODUCT_ID_RE.findall(text)))
    mode = classify_mode(text)
    need.slots["__mode__"] = mode
    need.evaluate = mode == "selection"
    return need


def required_slots(need: Need, plugin: DomainPlugin) -> list[str]:
    """澄清必填槽位（领域插件可声明 `agent_config.required_slots_by_mode`）。

    未声明时：领域若有槽位词典（选型型领域，如化学）→ 用通用默认；否则不强制澄清（如招商轻量装配）。
    """
    mode = need.slots.get("__mode__", "selection")
    mapping = plugin.agent_config.get("required_slots_by_mode")
    if mapping is not None:
        return list(mapping.get(mode, []))
    if plugin.agent_config.get("slot_dictionary"):
        return list(CRITICAL_BY_MODE.get(mode, []))
    return []


def merge_resume_answer(need: Need, answer: str, plugin: DomainPlugin) -> Need:
    """澄清答复合并：仅补齐缺失槽位，重算市场/场景/模式。"""
    extra = extract_need(answer, plugin, need.domain_id)
    for key, value in extra.slots.items():
        if key == "__mode__":
            continue
        if value and not need.slots.get(key):
            need.slots[key] = value
    need.fabric = need.slots.get("面料成分", need.fabric)
    if extra.target_performance:
        merged = list(dict.fromkeys(need.target_performance + extra.target_performance))
        need.target_performance = merged
        need.slots["目标性能"] = "、".join(merged)
    if need.market in ("unknown", "") and extra.market != "unknown":
        need.market = extra.market
        need.scenario = infer_scenario(need.market, f"{need.raw_text} {answer}")
    if extra.raw_text and not need.slots.get("成本约束"):
        # 答复中若含成本/市场关键词，分类仍以原始需求为准
        pass
    return need


# 会话滑窗可继承槽位（定位槽位；不继承产品点名，避免跨轮偏见；不继承工艺条件，其随面料而变）
HISTORY_INHERIT_SLOTS = ("面料成分", "目标性能", "目标市场", "成本约束")


def merge_history_context(need: Need, history: list[dict[str, str]], plugin: DomainPlugin) -> dict[str, str]:
    """多轮指代消解：从会话滑窗的历史用户消息补齐当前轮缺失的定位槽位（确定性规则）。

    - 就近优先：自最近的历史用户消息向前找，仅补缺失槽位，不覆盖当前轮已识别值；
    - 市场连续：当前轮未识别市场时，从最近命中市场的历史消息继承（保证合规判定不因省略而丢失）；
    - 场景：市场继承后按市场定画像；否则当前为「默认」时用最近一轮用户文本单跳推断（避免跨轮叠加）。
    返回：实际继承的槽位（含 `__market__` 时表示市场继承），供 trace 与输出注解。
    """
    inherited: dict[str, str] = {}
    user_texts = [m.get("content", "") for m in history if m.get("role") == "user" and m.get("content")]
    if not user_texts:
        return inherited

    for text in reversed(user_texts):
        prior = extract_need(text, plugin, need.domain_id)
        for key in HISTORY_INHERIT_SLOTS:
            if key not in need.slots and prior.slots.get(key):
                need.slots[key] = prior.slots[key]
                inherited[key] = prior.slots[key]
        if need.market == "unknown" and prior.market != "unknown":
            need.market = prior.market
            inherited["__market__"] = prior.market
        if all(k in need.slots for k in ("面料成分", "目标性能")) and need.market != "unknown":
            break

    if not inherited:
        return inherited
    need.fabric = need.slots.get("面料成分", need.fabric)
    if need.slots.get("目标性能"):
        need.target_performance = [p for p in need.slots["目标性能"].split("、") if p]
    if need.market != "unknown":
        need.scenario = infer_scenario(need.market, need.raw_text)
    elif need.scenario == "默认":
        need.scenario = infer_scenario(need.market, f"{user_texts[-1]} {need.raw_text}")
    return inherited
