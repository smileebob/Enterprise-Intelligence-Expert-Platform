"""知识域：入库任务（异步 + 幂等）、检索、冲突裁决（F4）、审核队列（F2）。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.config import get_settings
from app.db import get_db
from app.deps import assert_domain_access, idempotency_key, require_permissions
from core.abstraction.dtos import Actor
from core.growth.service import GrowthService
from core.knowledge.models import KbIngestTask
from core.knowledge.service import KnowledgeService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge"])


class IngestIn(BaseModel):
    domain_id: str = Field(default="chemical", max_length=64)
    source_name: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    source_type: str | None = None
    permission_level: int | None = Field(default=None, ge=1, le=3)


class RetrievalIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    domain_id: str = Field(default="chemical", max_length=64)
    top_k: int = Field(default=8, ge=1, le=30)


class ConflictResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=1000)


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


@router.post("/knowledge/documents", status_code=202, summary="知识入库（异步任务 + Idempotency-Key）")
def ingest_document(
    body: IngestIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
    key: str | None = Depends(idempotency_key),
) -> dict:
    # 前置校验：领域适配器声明的支持文件白名单（不支持的文件名回 400 而非任务内 500）
    from core.knowledge.ingest import get_ingest_adapter

    try:
        adapter = get_ingest_adapter(body.domain_id)
    except KeyError as exc:
        raise biz("VALIDATION_ERROR", f"领域未装配入库适配器：{body.domain_id}", 400) from exc
    assert_domain_access(actor, body.domain_id, db=db, require_online=False)
    checker = getattr(adapter, "supported_sources", None)
    if callable(checker):
        supported = tuple(checker())
        if supported and body.source_name not in supported:
            raise biz(
                "VALIDATION_ERROR",
                f"该领域不支持的知识文件：{body.source_name}（支持：{'、'.join(supported)}）",
                400,
            )

    idem = key or f"auto-{uuid.uuid4().hex[:16]}"

    # 幂等：Valkey SETNX 抢先，唯一索引兜底（规则 B-01）
    existing = db.execute(select(KbIngestTask).where(KbIngestTask.idempotency_key == idem)).scalars().first()
    if existing is not None:
        return ok({"task_id": existing.task_id, "status": existing.status, "duplicated": True})

    task_id = f"T-{uuid.uuid4().hex[:16]}"
    task = KbIngestTask(
        task_id=task_id,
        domain_id=body.domain_id,
        source_name=body.source_name,
        idempotency_key=idem,
        status="parsing",
        created_by=actor.user_id,
    )
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(KbIngestTask).where(KbIngestTask.idempotency_key == idem)).scalars().first()
        if existing is not None:
            return ok({"task_id": existing.task_id, "status": existing.status, "duplicated": True})
        raise

    # 暂存载荷（worker 读取；完成后清理）
    ingest_dir = Path(get_settings().DATA_DIR) / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    (ingest_dir / f"{task_id}.json").write_text(
        json.dumps(
            {
                "payload": body.payload,
                "source_type": body.source_type,
                "permission_level": body.permission_level,
                "actor_id": actor.user_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from core.tasks import ingest_document as ingest_task

    ingest_task.delay(task_id)
    db.refresh(task)
    return ok({"task_id": task.task_id, "status": task.status})


@router.get("/knowledge/documents/{task_id}", summary="入库任务进度")
def ingest_status(
    task_id: str,
    actor: Actor = Depends(require_permissions("retrieval.search")),
    db: Session = Depends(get_db),
) -> dict:
    task = db.get(KbIngestTask, task_id)
    if task is None:
        raise biz("DOCUMENT_NOT_FOUND", "文档任务不存在", 404)
    assert_domain_access(actor, task.domain_id, db=db, require_online=False)
    return ok(
        {
            "task_id": task.task_id,
            "domain_id": task.domain_id,
            "source_name": task.source_name,
            "status": task.status,
            "docs": task.docs,
            "chunks": task.chunks,
            "conflicts_detected": task.conflicts_detected,
            "pending_review": task.pending_review,
            "error": task.error,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        }
    )


@router.post("/retrieval/search", summary="混合检索（分区召回 + 融合；权限/分区服务端决定）")
async def retrieval_search(
    body: RetrievalIn,
    actor: Actor = Depends(require_permissions("retrieval.search")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, body.domain_id, db=db)
    service = KnowledgeService(db)
    fused = await service.retrieve(
        body.query,
        body.domain_id,
        permission_max=actor.permission_max,  # 服务端裁决，客户端不可指定（D8）
        top_k=body.top_k,
    )
    items = [
        {
            "chunk_id": e.chunk_id,
            "score": e.score,
            "source_type": e.source_type,
            "confidence": e.confidence,
            "source_ref": e.source_ref,
            "content": e.content,
            "citation_id": e.citation_id,
            "category": e.category,
            "record_id": (e.meta or {}).get("record_id", ""),
        }
        for e in fused.evidence
    ]
    return ok(
        {
            "items": items,
            "partition_stats": fused.partition_stats,
            "conflicts": [
                {"conflict_id": c.conflict_id, "title": c.title, "status": c.status} for c in fused.conflicts
            ],
            "override_flags": fused.override_flags,
            "meta": {"degrade_level": "L0", "notes": fused.notes},
        }
    )


@router.get("/knowledge/conflicts", summary="冲突列表（F4 裁决队列）")
def list_conflicts(
    domain_id: str = "chemical",
    status: str | None = None,
    actor: Actor = Depends(require_permissions("knowledge.read")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, domain_id, db=db, require_online=False)
    conflicts = KnowledgeService(db).list_conflicts(domain_id, status)
    return ok(
        [
            {
                "conflict_id": c.conflict_id,
                "title": c.title,
                "topic": c.topic,
                "conflict_type": c.conflict_type,
                "internal_claim": {"statement": c.internal_claim.statement, "refs": c.internal_claim.refs, "confidence": c.internal_claim.confidence},
                "external_claim": {"statement": c.external_claim.statement, "refs": c.external_claim.refs, "confidence": c.external_claim.confidence},
                "expected_resolution": c.expected_resolution,
                "status": c.status,
                "resolution": c.resolution,
            }
            for c in conflicts
        ]
    )


@router.post("/knowledge/conflicts/{conflict_id}/resolve", summary="冲突裁决（知识管理员）")
def resolve_conflict(
    conflict_id: str,
    body: ConflictResolveIn,
    actor: Actor = Depends(require_permissions("conflict.resolve")),
    db: Session = Depends(get_db),
) -> dict:
    service = KnowledgeService(db)
    conflict = service.get_conflict(conflict_id)
    if conflict is None:
        raise biz("CONFLICT_NOT_FOUND", "冲突记录不存在", 404)
    assert_domain_access(actor, conflict.domain_id, db=db, require_online=False)
    service.resolve_conflict(
        conflict_id,
        {"resolution": body.resolution, "note": body.note, "resolved_by": actor.user_id},
    )
    return ok({"conflict_id": conflict_id, "status": "resolved", "resolution": body.resolution})


@router.get("/knowledge/review-queue", summary="案例审核队列（正样本回流，BR-06）")
def review_queue(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
) -> dict:
    assert_domain_access(actor, domain_id, db=db, require_online=False)
    return ok(GrowthService(db).review_queue(domain_id))


@router.post("/knowledge/review/{feedback_id}", summary="案例审核（approve/reject）")
def review_case(
    feedback_id: str,
    body: ReviewIn,
    actor: Actor = Depends(require_permissions("knowledge.write")),
    db: Session = Depends(get_db),
) -> dict:
    from core.growth.models import GrowthFeedback

    feedback = db.get(GrowthFeedback, feedback_id)
    if feedback is None:
        raise biz("FEEDBACK_NOT_FOUND", "反馈不存在", 404)
    assert_domain_access(actor, feedback.domain_id, db=db, require_online=False)
    return ok(GrowthService(db).review_case(feedback_id, body.action, actor))
