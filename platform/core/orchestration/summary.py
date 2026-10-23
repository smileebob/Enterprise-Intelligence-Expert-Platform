"""会话摘要（04 §4）：确定性槽位摘要 + 版本 CAS + 下一轮真实消费。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.orchestration.models import ChatSessionSummary
from core.orchestration.state import Need

MAX_COVERED_IDS = 100
MAX_TOPICS = 5
SLOT_KEYS = ("面料成分", "目标性能", "目标市场", "成本约束")


class SessionSummaryService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, session_id: str) -> ChatSessionSummary | None:
        return (
            self.db.execute(
                select(ChatSessionSummary).where(ChatSessionSummary.session_id == session_id)
            )
            .scalars()
            .first()
        )

    def record_turn(
        self,
        *,
        session_id: str,
        owner_id: str,
        domain_id: str,
        need: Need | None,
        user_text: str,
        user_message_id: str = "",
        assistant_message_id: str = "",
    ) -> ChatSessionSummary | None:
        """只压缩已完成回合；CAS 覆盖版本，失败重试而不是丢新消息。"""
        new_ids = [item for item in (user_message_id, assistant_message_id) if item]
        for _ in range(3):
            current = self.get(session_id)
            previous_slots = dict(current.structured_slots or {}) if current else {}
            base_ids = list(current.covered_message_ids or []) if current is not None else []
            covered = list(dict.fromkeys(base_ids + new_ids))[-MAX_COVERED_IDS:]
            slots = dict(previous_slots)
            if need is not None:
                for key, value in need.slots.items():
                    if key.startswith("__"):
                        continue
                    if value:
                        slots[key] = value
            topics = [str(item) for item in (previous_slots.get("__topics__") or [])]
            if user_text.strip():
                topics.append(user_text.strip()[:60])
            topics = topics[-MAX_TOPICS:]
            slots["__topics__"] = topics
            summary_text = self._render(slots, need)
            version = (current.version + 1) if current else 1
            if current is None:
                self.db.add(
                    ChatSessionSummary(
                        summary_id=f"SUM-{uuid.uuid4().hex[:16]}",
                        session_id=session_id,
                        owner_id=owner_id,
                        domain_id=domain_id,
                        version=version,
                        covered_message_ids=covered,
                        structured_slots=slots,
                        summary_text=summary_text,
                        source="deterministic",
                    )
                )
                try:
                    self.db.commit()
                    return self.get(session_id)
                except IntegrityError:
                    self.db.rollback()
                    continue
            result = self.db.execute(
                update(ChatSessionSummary)
                .where(
                    ChatSessionSummary.session_id == session_id,
                    ChatSessionSummary.version == current.version,
                )
                .values(
                    version=version,
                    covered_message_ids=covered,
                    structured_slots=slots,
                    summary_text=summary_text,
                    owner_id=owner_id,
                    domain_id=domain_id,
                )
            )
            if getattr(result, "rowcount", 0) == 1:
                self.db.commit()
                return self.get(session_id)
            self.db.rollback()
        return None

    @staticmethod
    def _render(slots: dict[str, Any], need: Need | None) -> str:
        scenario = need.scenario if need is not None else "默认"
        parts = [f"场景={scenario}"]
        for key in SLOT_KEYS:
            if slots.get(key):
                parts.append(f"{key}={slots[key]}")
        topics = slots.get("__topics__") or []
        if topics:
            parts.append("近期问题=" + " / ".join(topics[-3:]))
        return "会话摘要（确定性）：" + "；".join(parts)

    def apply_to_need(self, session_id: str, need: Need) -> dict[str, str]:
        """把摘要中的定位槽位补进当前需求，只补缺失值，不覆盖本轮识别结果。"""
        summary = self.get(session_id)
        if summary is None or not summary.structured_slots:
            return {}
        inherited: dict[str, str] = {}
        for key in SLOT_KEYS:
            value = summary.structured_slots.get(key)
            if value and key not in need.slots:
                need.slots[key] = str(value)
                inherited[key] = str(value)
        if need.slots.get("面料成分"):
            need.fabric = need.slots["面料成分"]
        if need.slots.get("目标性能"):
            need.target_performance = [p for p in need.slots["目标性能"].split("、") if p]
        market_slot = str(need.slots.get("目标市场") or summary.structured_slots.get("目标市场") or "")
        if need.market == "unknown" and market_slot:
            if "欧盟" in market_slot or "出口" in market_slot:
                need.market = "eu"
            elif "内销" in market_slot or "国内" in market_slot:
                need.market = "domestic"
        return inherited
