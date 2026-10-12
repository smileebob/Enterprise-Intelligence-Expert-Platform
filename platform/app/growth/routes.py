"""自成长域：纠错规则查看、标准更新草稿/发布、标注数据导出（P2 预留）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import require_permissions
from core.abstraction.dtos import Actor
from core.growth.service import GrowthService

router = APIRouter(tags=["growth"])


class ProposeIn(BaseModel):
    domain_id: str = Field(default="chemical", max_length=64)
    note: str = Field(default="", max_length=500)


class PublishIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=64)


@router.get("/growth/correction-rules", summary="纠错规则库（负样本沉淀，注入 System 约束）")
def list_rules(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("growth.export")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(GrowthService(db).list_correction_rules(domain_id))


@router.post("/growth/standards/propose", summary="生成标准更新草稿（draft）")
def propose_standard(
    body: ProposeIn,
    actor: Actor = Depends(require_permissions("eval.standard.manage")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(GrowthService(db).propose_standard_update(body.domain_id, actor, note=body.note))
    except KeyError as exc:
        raise biz("STANDARD_NOT_FOUND", "领域无现行标准", 404) from exc


@router.post("/growth/standards/{standard_id}/publish", summary="审核发布（旧版 archived，历史评分不重算 BR-09）")
def publish_standard(
    standard_id: str,
    body: PublishIn,
    actor: Actor = Depends(require_permissions("eval.standard.manage")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(GrowthService(db).publish_standard(standard_id, body.reviewer, actor))
    except KeyError as exc:
        raise biz("STANDARD_NOT_FOUND", "标准草稿不存在", 404) from exc
    except ValueError as exc:
        raise biz("STATE_INVALID", str(exc), 409) from exc


@router.post("/growth/datasets/export", summary="标注数据导出（P2 预留，本次不训练）")
def export_dataset(
    body: ProposeIn,
    actor: Actor = Depends(require_permissions("growth.export")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(GrowthService(db).export_dataset(body.domain_id, actor))
