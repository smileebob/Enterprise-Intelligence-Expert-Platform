"""编排状态与产出结构（技术方案 03 §3.2 State schema）。

状态需可 JSON 序列化（检查点落库，中断恢复/断点续跑/审计三合一）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from core.abstraction.dtos import ConflictRecord, Evidence


@dataclass
class Need:
    """结构化需求（澄清槽位 + 路由要素）。"""

    raw_text: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    domain_id: str = ""
    market: str = "unknown"
    scenario: str = "默认"
    fabric: str = ""
    target_performance: list[str] = field(default_factory=list)
    product_ids: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    evaluate: bool = True  # 是否触发六维评估（选型类问题）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Need:
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class Claim:
    text: str
    citation_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "citation_ids": list(self.citation_ids)}


@dataclass
class DraftSection:
    title: str
    claims: list[Claim] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "claims": [c.to_dict() for c in self.claims]}


@dataclass
class Draft:
    """结构化草稿：每条结论挂 citation_id（BR-02 强制引用）。"""

    summary: str = ""
    sections: list[DraftSection] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)  # citation 明细（SSE/持久化）
    conflicts: list[ConflictRecord] = field(default_factory=list)
    eval_result: dict[str, Any] | None = None
    confidence: float = 0.0
    compliance_flags: list[dict[str, Any]] = field(default_factory=list)
    override_flags: list[dict[str, Any]] = field(default_factory=list)
    degrade_level: str = "L0"
    annotations: list[str] = field(default_factory=list)  # 输出 Hook 追加（合规/低置信标注）
    scored_by: str = ""

    def all_claims(self) -> list[Claim]:
        claims: list[Claim] = []
        for section in self.sections:
            claims.extend(section.claims)
        return claims


@dataclass
class StepTrace:
    node: str
    status: str = "done"
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnState:
    """一次对话回合的完整状态（序列化 = 检查点）。"""

    session_id: str = ""
    turn_id: str = ""
    user_id: str = ""
    role_id: str = ""
    domain_id: str = "chemical"
    message_id: str = ""          # 本轮用户消息 ID（assistant message 在完成时生成）
    assistant_message_id: str = ""
    user_msg: str = ""            # 已脱敏
    history: list[dict[str, str]] = field(default_factory=list)  # 会话滑窗（[{"role","content"}]，最近在后）
    need: Need | None = None
    plan: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    eval_result: dict[str, Any] | None = None
    draft: Draft | None = None
    self_check: dict[str, Any] = field(default_factory=dict)
    replan_count: int = 0
    degrade_level: str = "L0"
    trace: list[StepTrace] = field(default_factory=list)
    correction_rules: list[str] = field(default_factory=list)
    memory_items: list[dict[str, Any]] = field(default_factory=list)
    interrupted: bool = False
    interrupt_kind: str = ""
    interrupt_payload: dict[str, Any] = field(default_factory=dict)
    resume_value: Any = None

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "user_id": self.user_id,
            "role_id": self.role_id,
            "domain_id": self.domain_id,
            "message_id": self.message_id,
            "assistant_message_id": self.assistant_message_id,
            "user_msg": self.user_msg,
            "history": self.history,
            "need": self.need.to_dict() if self.need else None,
            "plan": self.plan,
            "evidence": [
                {
                    "chunk_id": e.chunk_id,
                    "content": e.content,
                    "source_type": e.source_type,
                    "confidence": e.confidence,
                    "citation_id": e.citation_id,
                    "source_ref": e.source_ref,
                    "score": e.score,
                    "category": e.category,
                    "meta": e.meta,
                }
                for e in self.evidence
            ],
            "conflicts": [c.conflict_id for c in self.conflicts],  # 明细从库加载
            "eval_result": self.eval_result,
            "draft": None
            if self.draft is None
            else {
                "summary": self.draft.summary,
                "sections": [s.to_dict() for s in self.draft.sections],
                "citations": self.draft.citations,
                "confidence": self.draft.confidence,
                "compliance_flags": self.draft.compliance_flags,
                "override_flags": self.draft.override_flags,
                "degrade_level": self.draft.degrade_level,
                "annotations": self.draft.annotations,
                "scored_by": self.draft.scored_by,
            },
            "self_check": self.self_check,
            "replan_count": self.replan_count,
            "degrade_level": self.degrade_level,
            "trace": [t.to_dict() for t in self.trace],
            "correction_rules": self.correction_rules,
            "memory_items": self.memory_items,
            "interrupted": self.interrupted,
            "interrupt_kind": self.interrupt_kind,
            "interrupt_payload": self.interrupt_payload,
            "resume_value": self.resume_value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnState:
        state = cls()
        for key in (
            "session_id",
            "turn_id",
            "user_id",
            "role_id",
            "domain_id",
            "message_id",
            "assistant_message_id",
            "user_msg",
            "history",
            "plan",
            "eval_result",
            "self_check",
            "replan_count",
            "degrade_level",
            "correction_rules",
            "memory_items",
            "interrupted",
            "interrupt_kind",
            "interrupt_payload",
            "resume_value",
        ):
            if key in data:
                setattr(state, key, data[key])
        if data.get("need"):
            state.need = Need.from_dict(data["need"])
        state.evidence = [
            Evidence(
                chunk_id=e["chunk_id"],
                content=e["content"],
                source_type=e["source_type"],
                confidence=e.get("confidence"),
                citation_id=e.get("citation_id", ""),
                source_ref=e.get("source_ref", ""),
                score=e.get("score", 0.0),
                category=e.get("category"),
                meta=e.get("meta", {}),
            )
            for e in data.get("evidence", [])
        ]
        draft = data.get("draft")
        if draft:
            state.draft = Draft(
                summary=draft.get("summary", ""),
                sections=[
                    DraftSection(title=s["title"], claims=[Claim(**c) for c in s.get("claims", [])])
                    for s in draft.get("sections", [])
                ],
                citations=draft.get("citations", []),
                confidence=draft.get("confidence", 0.0),
                compliance_flags=draft.get("compliance_flags", []),
                override_flags=draft.get("override_flags", []),
                degrade_level=draft.get("degrade_level", "L0"),
                annotations=draft.get("annotations", []),
                scored_by=draft.get("scored_by", ""),
            )
        state.trace = [StepTrace(**{k: v for k, v in t.items() if k in StepTrace.__dataclass_fields__}) for t in data.get("trace", [])]
        return state
