"""评估域：六维评估（幂等）、结果查询、人工确认/修正、标准版本回溯（BR-09）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import idempotency_key, require_permissions
from core.abstraction.dtos import Actor
from core.cache import cache_add, cache_delete, ckey
from core.evaluation.engine import EvaluationEngine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["evaluation"])


class EvaluateIn(BaseModel):
    domain_id: str = Field(default="chemical", max_length=64)
    object_type: str = Field(default="solution", max_length=32)
    object_ref: dict[str, Any]
    scenario: str = Field(default="默认", max_length=64)
    need_ref: str | None = Field(default=None, max_length=128)
    conflicts: list[dict[str, Any]] | None = None
    force_calibrate: bool = False


class ConfirmIn(BaseModel):
    action: str = Field(pattern="^(confirm|modify)$")
    corrections: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)


@router.get("/evaluations/standards", summary="领域标准版本列表（BR-09 旧版可回溯）")
def list_standards(
    domain_id: str | None = None,
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    from core.growth.service import GrowthService

    return ok(GrowthService(db).list_standards(domain_id))


@router.get("/evaluations", summary="评估结果列表")
def list_evaluations(
    domain_id: str | None = None,
    limit: int = 20,
    actor: Actor = Depends(require_permissions("eval.run")),
    db: Session = Depends(get_db),
) -> dict:
    from core.runtime import eval_repo

    return ok(eval_repo(db).list_results(domain_id, limit=min(max(limit, 1), 100)))


@router.post("/evaluations", status_code=201, summary="执行六维评估（Idempotency-Key 必填）")
async def evaluate(
    body: EvaluateIn,
    actor: Actor = Depends(require_permissions("eval.run")),
    db: Session = Depends(get_db),
    key: str | None = Depends(idempotency_key),
) -> dict:
    if not key:
        raise biz("VALIDATION_ERROR", "Idempotency-Key 请求头必填", 400)
    lock_key = ckey("eval", "idem", key)
    if not await cache_add(lock_key, "1", ttl=600):
        from core.runtime import eval_repo

        existing = eval_repo(db).get_result_by_idempotency(key)
        if existing is not None:
            return ok(existing)
        raise biz("IDEMPOTENT_DUPLICATE", "重复请求，请勿重复提交", 409)

    from core.runtime import get_db_domain_registry

    engine = EvaluationEngine(db, registry=get_db_domain_registry(db))
    try:
        result = await engine.evaluate(
            {
                "domain_id": body.domain_id,
                "object_type": body.object_type,
                "object_ref": body.object_ref,
                "scenario": body.scenario,
                "need_ref": body.need_ref,
                "conflicts": body.conflicts or [],
                "force_calibrate": body.force_calibrate,
            },
            actor=actor,
            idempotency_key=key,
            persist=True,
        )
    except Exception:
        await cache_delete(lock_key)  # 失败允许重试同一幂等键
        raise
    return ok(result)


@router.get("/evaluations/{eval_id}", summary="评估结果详情")
def get_evaluation(
    eval_id: str,
    actor: Actor = Depends(require_permissions("eval.run")),
    db: Session = Depends(get_db),
) -> dict:
    from core.runtime import eval_repo

    result = eval_repo(db).get_result(eval_id)
    if result is None:
        raise biz("EVAL_NOT_FOUND", "评估结果不存在", 404)
    return ok(result)


@router.post("/evaluations/{eval_id}/confirm", summary="人工确认/修正（回流自成长）")
def confirm_evaluation(
    eval_id: str,
    body: ConfirmIn,
    actor: Actor = Depends(require_permissions("eval.confirm")),
    db: Session = Depends(get_db),
) -> dict:
    engine = EvaluationEngine(db)
    try:
        result = engine.confirm(eval_id, {"action": body.action, "corrections": body.corrections, "reason": body.reason}, actor)
    except KeyError as exc:
        raise biz("EVAL_NOT_FOUND", "评估结果不存在", 404) from exc
    return ok(result)
