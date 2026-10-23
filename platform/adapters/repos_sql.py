"""记忆/评估/审计仓储 · ORM 通用实现（SQLite 与 PostgreSQL 同构）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.storage_sqlite import cosine
from core.abstraction.dtos import AuditEntry, EvalStandardRecord, MemoryItemRecord
from core.clock import iso, utc_now
from core.evaluation.models import EvalResult, EvalStandard
from core.gateway.models import GovAuditLog
from core.memory.models import MemEvent, MemItem


# ---------------- 记忆 ----------------
def _mem_to_dto(row: MemItem) -> MemoryItemRecord:
    return MemoryItemRecord(
        memory_id=row.memory_id,
        layer=row.layer,
        owner_id=row.owner_id,
        content=row.content,
        domain_id=row.domain_id,
        permission_scope=row.permission_scope,
        source_type=row.source_type,
        confidence=float(row.confidence) if row.confidence is not None else 0.6,
        reuse_count=row.reuse_count,
        last_used_at=iso(row.last_used_at),
        promoted_from=row.promoted_from,
        promotion_basis=row.promotion_basis,
        derived_from=row.derived_from,
        status=row.status,
        embedding=row.embedding,
        meta=row.meta or {},
    )


class SqlMemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_item(self, item: MemoryItemRecord) -> None:
        row = self.db.get(MemItem, item.memory_id)
        if row is None:
            row = MemItem(memory_id=item.memory_id)
            self.db.add(row)
        row.layer = item.layer
        row.owner_id = item.owner_id
        row.domain_id = item.domain_id
        row.permission_scope = item.permission_scope
        row.content = item.content
        row.embedding = item.embedding
        row.source_type = item.source_type
        row.confidence = item.confidence
        row.reuse_count = item.reuse_count
        row.promoted_from = item.promoted_from
        row.promotion_basis = item.promotion_basis
        row.derived_from = item.derived_from
        row.status = item.status
        row.meta = item.meta
        self.db.flush()

    def get_item(self, memory_id: str) -> MemoryItemRecord | None:
        row = self.db.get(MemItem, memory_id)
        return _mem_to_dto(row) if row else None

    def list_items(
        self,
        layer: str | None = None,
        owner_id: str | None = None,
        domain_id: str | None = None,
        status: str | None = "active",
        limit: int = 50,
    ) -> list[MemoryItemRecord]:
        stmt = select(MemItem)
        if layer:
            stmt = stmt.where(MemItem.layer == layer)
        if owner_id:
            stmt = stmt.where(MemItem.owner_id == owner_id)
        if domain_id:
            stmt = stmt.where(MemItem.domain_id == domain_id)
        if status:
            stmt = stmt.where(MemItem.status == status)
        rows = self.db.execute(stmt.order_by(MemItem.created_at.desc()).limit(limit)).scalars().all()
        return [_mem_to_dto(r) for r in rows]

    def similar_items(
        self,
        owner_id: str,
        layer: str,
        embedding: list[float],
        threshold: float = 0.9,
        domain_id: str | None = None,
    ) -> list[MemoryItemRecord]:
        candidates = self.list_items(
            layer=layer, owner_id=owner_id, domain_id=domain_id, status="active", limit=200
        )
        matched: list[tuple[float, MemoryItemRecord]] = []
        for item in candidates:
            if item.embedding:
                sim = cosine(embedding, item.embedding)
                if sim >= threshold:
                    matched.append((sim, item))
        matched.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in matched]

    def add_event(self, memory_id: str, event_type: str, detail: dict[str, Any], actor_id: str | None) -> None:
        self.db.add(MemEvent(memory_id=memory_id, event_type=event_type, detail=detail, actor_id=actor_id))
        self.db.flush()

    def list_events(self, memory_id: str) -> list[dict[str, Any]]:
        rows = (
            self.db.execute(
                select(MemEvent).where(MemEvent.memory_id == memory_id).order_by(MemEvent.ts.asc())
            )
            .scalars()
            .all()
        )
        return [
            {"event_type": r.event_type, "detail": r.detail, "actor_id": r.actor_id, "ts": iso(r.ts)}
            for r in rows
        ]

    def delete_item(self, memory_id: str) -> None:
        row = self.db.get(MemItem, memory_id)
        if row is not None:
            self.db.delete(row)
            self.db.flush()


# ---------------- 评估 ----------------
def _standard_to_dto(row: EvalStandard) -> EvalStandardRecord:
    return EvalStandardRecord(
        standard_id=row.standard_id,
        domain_id=row.domain_id,
        year=row.year,
        object_type=row.object_type,
        sub_domain=row.sub_domain,
        standard_desc=row.standard_desc,
        dimension_weights=row.dimension_weights or {},
        scoring_rubric=row.scoring_rubric or {},
        scenario_profiles=row.scenario_profiles or {},
        sample_size=row.sample_size,
        version=row.version,
        status=row.status,
        reviewed_by=row.reviewed_by,
        meta=row.meta or {},
    )


class SqlEvalRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_standard(self, standard: EvalStandardRecord) -> None:
        row = self.db.get(EvalStandard, standard.standard_id)
        if row is None:
            row = EvalStandard(standard_id=standard.standard_id)
            self.db.add(row)
        row.domain_id = standard.domain_id
        row.year = standard.year
        row.object_type = standard.object_type
        row.sub_domain = standard.sub_domain
        row.standard_desc = standard.standard_desc
        row.dimension_weights = standard.dimension_weights
        row.scoring_rubric = standard.scoring_rubric
        row.scenario_profiles = standard.scenario_profiles
        row.sample_size = standard.sample_size
        row.version = standard.version
        row.status = standard.status
        row.reviewed_by = standard.reviewed_by
        row.meta = standard.meta
        self.db.flush()

    def get_standard(self, standard_id: str) -> EvalStandardRecord | None:
        row = self.db.get(EvalStandard, standard_id)
        return _standard_to_dto(row) if row else None

    def get_active_standard(self, domain_id: str, object_type: str) -> EvalStandardRecord | None:
        row = (
            self.db.execute(
                select(EvalStandard)
                .where(
                    EvalStandard.domain_id == domain_id,
                    EvalStandard.object_type == object_type,
                    EvalStandard.status == "active",
                )
                .order_by(EvalStandard.created_at.desc())
            )
            .scalars()
            .first()
        )
        return _standard_to_dto(row) if row else None

    def list_standards(self, domain_id: str | None = None) -> list[EvalStandardRecord]:
        stmt = select(EvalStandard)
        if domain_id:
            stmt = stmt.where(EvalStandard.domain_id == domain_id)
        rows = self.db.execute(stmt.order_by(EvalStandard.created_at.desc())).scalars().all()
        return [_standard_to_dto(r) for r in rows]

    def save_result(self, result: dict[str, Any]) -> None:
        row = self.db.get(EvalResult, result["eval_id"])
        if row is None:
            row = EvalResult(eval_id=result["eval_id"])
            self.db.add(row)
        row.domain_id = result.get("domain_id", "")
        row.object_type = result.get("object_type", "solution")
        row.object_ref = result.get("object_ref", {})
        row.standard_id = result.get("standard_id", "")
        row.scenario = result.get("scenario", "")
        row.dimension_scores = result.get("dimension_scores", {})
        row.total_score = result.get("total_score")
        row.confidence = result.get("confidence")
        row.calibration = result.get("calibration")
        row.risk_flags = result.get("risk_flags", [])
        row.compliance_flags = result.get("compliance_flags", [])
        row.guardrails = result.get("guardrails", [])
        row.scored_by = result.get("scored_by", "rule+llm")
        row.confirmed_by = result.get("confirmed_by")
        row.confirm_action = result.get("confirm_action")
        row.idempotency_key = result.get("idempotency_key")
        row.trace_id = result.get("trace_id")
        self.db.flush()

    def get_result(self, eval_id: str) -> dict[str, Any] | None:
        row = self.db.get(EvalResult, eval_id)
        if row is None:
            return None
        return {
            "eval_id": row.eval_id,
            "domain_id": row.domain_id,
            "object_type": row.object_type,
            "object_ref": row.object_ref or {},
            "standard_id": row.standard_id,
            "standard_version": row.standard_id,  # 展示口径：标准版本（standard_id 即版本标识）
            "scenario": row.scenario,
            "dimension_scores": row.dimension_scores or {},
            "total_score": float(row.total_score) if row.total_score is not None else None,
            "confidence": float(row.confidence) if row.confidence is not None else None,
            "calibration": row.calibration,
            "risk_flags": row.risk_flags or [],
            "compliance_flags": row.compliance_flags or [],
            "guardrails": row.guardrails or [],
            "scored_by": row.scored_by,
            "confirmed_by": row.confirmed_by,
            "confirm_action": row.confirm_action,
            "trace_id": row.trace_id,
            "scored_at": iso(row.scored_at),
        }

    def get_result_by_idempotency(self, key: str) -> dict[str, Any] | None:
        row = (
            self.db.execute(select(EvalResult).where(EvalResult.idempotency_key == key))
            .scalars()
            .first()
        )
        return self.get_result(row.eval_id) if row else None

    def list_results(self, domain_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        stmt = select(EvalResult)
        if domain_id:
            stmt = stmt.where(EvalResult.domain_id == domain_id)
        rows = self.db.execute(stmt.order_by(EvalResult.scored_at.desc()).limit(limit)).scalars().all()
        return [self.get_result(r.eval_id) for r in rows if r is not None]


# ---------------- 审计 ----------------
class SqlAuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def append(self, entry: AuditEntry) -> None:
        self.db.add(
            GovAuditLog(
                trace_id=entry.trace_id,
                actor_id=entry.actor_id,
                action=entry.action,
                resource=entry.resource,
                hook_type=entry.hook_type,
                decision=entry.decision,
                detail=entry.detail,
            )
        )
        self.db.flush()

    def query(
        self,
        trace_id: str | None = None,
        hook_type: str | None = None,
        actor_id: str | None = None,
        ts_from: str | None = None,
        ts_to: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        stmt = select(GovAuditLog)
        if trace_id:
            stmt = stmt.where(GovAuditLog.trace_id == trace_id)
        if hook_type:
            stmt = stmt.where(GovAuditLog.hook_type == hook_type)
        if actor_id:
            stmt = stmt.where(GovAuditLog.actor_id == actor_id)
        if ts_from:
            stmt = stmt.where(GovAuditLog.ts >= ts_from)
        if ts_to:
            stmt = stmt.where(GovAuditLog.ts <= ts_to)
        rows = self.db.execute(stmt.order_by(GovAuditLog.ts.desc()).limit(limit)).scalars().all()
        return [
            {
                "log_id": r.log_id,
                "trace_id": r.trace_id,
                "actor_id": r.actor_id,
                "action": r.action,
                "resource": r.resource,
                "hook_type": r.hook_type,
                "decision": r.decision,
                "detail": r.detail,
                "ts": iso(r.ts),
            }
            for r in rows
        ]


__all__ = ["SqlMemoryRepository", "SqlEvalRepository", "SqlAuditRepository", "utc_now"]
