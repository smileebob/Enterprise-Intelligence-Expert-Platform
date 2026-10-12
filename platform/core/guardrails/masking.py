"""按角色动态脱敏规则表（BR-08）—— 界面/API/日志三处共用，治理域配置承载、可热更新。

敏感字段类型（配方配比/报价/客户联系方式）× 角色 → 脱敏方式（mask/deny/allow）。
"""

from __future__ import annotations

import re

from core.abstraction.enums import Roles

# 角色 → 敏感字段访问权限
MASKING_RULES: dict[str, dict[str, str]] = {
    "配方配比": {
        Roles.KB_ADMIN: "allow",
        Roles.PLATFORM_ADMIN: "allow",
        Roles.EXPERT: "mask",
        Roles.SEC_AUDITOR: "mask",
        Roles.AGENT: "allow",  # 智能体内部推理可见，对外输出经输出 Hook 再脱敏
    },
    "报价": {
        Roles.KB_ADMIN: "mask",
        Roles.PLATFORM_ADMIN: "allow",
        Roles.EXPERT: "mask",
        Roles.SEC_AUDITOR: "mask",
        Roles.AGENT: "mask",
    },
    "客户联系方式": {
        Roles.KB_ADMIN: "mask",
        Roles.PLATFORM_ADMIN: "mask",
        Roles.EXPERT: "mask",
        Roles.SEC_AUDITOR: "mask",
        Roles.AGENT: "mask",
    },
}

MASK_TOKEN = "•••"  # noqa: S105

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_AMOUNT_GPL_RE = re.compile(r"\d+(?:\.\d+)?\s*g/L")
_PRICE_RE = re.compile(r"[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元")


def mask_pii(text: str) -> str:
    """通用 PII 脱敏（手机号/邮箱），输入与输出 Hook 共用。"""
    if not text:
        return text
    text = _PHONE_RE.sub(r"\1****\2", text)
    text = _EMAIL_RE.sub(r"\1***\2", text)
    return text


def detect_pii(text: str) -> list[str]:
    hits: list[str] = []
    if _PHONE_RE.search(text or ""):
        hits.append("手机号")
    if _EMAIL_RE.search(text or ""):
        hits.append("邮箱")
    return hits


def mask_by_type(text: str, field_type: str) -> str:
    """按敏感字段类型脱敏文本（不改动非敏感数字）。"""
    if field_type == "配方配比":
        return _AMOUNT_GPL_RE.sub(MASK_TOKEN, text)
    if field_type == "报价":
        return _PRICE_RE.sub(MASK_TOKEN, text)
    if field_type == "客户联系方式":
        return mask_pii(text)
    return mask_pii(text)


def role_can_view(role_id: str, field_type: str) -> bool:
    rule = MASKING_RULES.get(field_type, {})
    return rule.get(role_id, "mask") == "allow"


def mask_text_for_role(text: str, role_id: str, sensitive_fields: list[str]) -> tuple[str, list[str]]:
    """按角色与命中敏感字段脱敏；返回（脱敏文本, 实际脱敏字段列表）。"""
    masked_fields: list[str] = []
    result = text or ""
    for field_type in sensitive_fields:
        if not role_can_view(role_id, field_type):
            new_text = mask_by_type(result, field_type)
            if new_text != result:
                masked_fields.append(field_type)
            result = new_text
    return result, masked_fields
