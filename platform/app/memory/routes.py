"""记忆域：PIPL 个人记忆（查阅/更正/删除/可携带导出）+ 企业级记忆与晋升（F5）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import require_permissions
from core.abstraction.dtos import Actor
from core.abstraction.enums import MemoryLayer
from core.memory.service import MemoryService

router = APIRouter(tags=["memory"])


class MemoryUpdateIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class PromoteIn(BaseModel):
    basis: str = Field(min_length=1, max_length=256)


def _item_view(item) -> dict:
    return {
        "memory_id": item.memory_id,
        "layer": item.layer,
        "content": item.content,
        "domain_id": item.domain_id,
        "permission_scope": item.permission_scope,
        "source_type": item.source_type,
        "confidence": item.confidence,
        "reuse_count": item.reuse_count,
        "status": item.status,
        "promoted_from": item.promoted_from,
        "promotion_basis": item.promotion_basis,
    }


@router.get("/memory/mine", summary="本人记忆查阅（PIPL）")
def list_mine(
    actor: Actor = Depends(require_permissions("memory.mine.read")),
    db: Session = Depends(get_db),
) -> dict:
    items = MemoryService(db).list_mine(actor.user_id)
    return ok([_item_view(item) for item in items])


@router.get("/memory/mine/export", summary="本人记忆导出（可携带）")
def export_mine(
    actor: Actor = Depends(require_permissions("memory.mine.read")),
    db: Session = Depends(get_db),
) -> dict:
    items = MemoryService(db).list_mine(actor.user_id)
    return ok({"user_id": actor.user_id, "count": len(items), "items": [_item_view(item) for item in items]})


@router.put("/memory/mine/{memory_id}", summary="本人记忆更正")
def update_mine(
    memory_id: str,
    body: MemoryUpdateIn,
    actor: Actor = Depends(require_permissions("memory.mine.write")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = MemoryService(db).update_mine(actor.user_id, memory_id, body.content)
    except KeyError as exc:
        raise biz("MEMORY_NOT_FOUND", "记忆不存在或无权访问", 404) from exc
    return ok(_item_view(item))


@router.delete("/memory/mine/{memory_id}", summary="本人记忆删除（写 mem_event 留痕）")
def delete_mine(
    memory_id: str,
    actor: Actor = Depends(require_permissions("memory.mine.write")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        MemoryService(db).delete_mine(actor.user_id, memory_id)
    except KeyError as exc:
        raise biz("MEMORY_NOT_FOUND", "记忆不存在或无权访问", 404) from exc
    return ok({"memory_id": memory_id, "deleted": True})


@router.get("/memory/enterprise", summary="企业级记忆（授权范围）")
def list_enterprise(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("memory.read")),
    db: Session = Depends(get_db),
) -> dict:
    items = MemoryService(db).repo.list_items(layer=MemoryLayer.ENTERPRISE, domain_id=domain_id, status="active", limit=100)
    return ok([_item_view(item) for item in items])


@router.get("/memory/promotion-candidates", summary="晋升候选（≥2 用户同向验证）")
def promotion_candidates(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("memory.enterprise.manage")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(MemoryService(db).promotion_candidates(domain_id))


@router.post("/memory/{memory_id}/promote", summary="晋升为企业级（管理员确认）")
def promote(
    memory_id: str,
    body: PromoteIn,
    actor: Actor = Depends(require_permissions("memory.enterprise.manage")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = MemoryService(db).promote(memory_id, body.basis, actor)
    except KeyError as exc:
        raise biz("MEMORY_NOT_FOUND", "记忆不存在", 404) from exc
    return ok(_item_view(item))
