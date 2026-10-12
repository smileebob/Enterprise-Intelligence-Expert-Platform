"""领域装配域（F7/M9）：domain.yaml 提交/校验/发布/回滚/查询。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import require_permissions
from core.abstraction.dtos import Actor
from core.admin.service import AdminService

router = APIRouter(tags=["domains"])


class DomainSubmitIn(BaseModel):
    config: dict[str, Any] = Field(description="domain.yaml 定稿（五要素契约，02 §2.4）")


@router.post("/domains", status_code=201, summary="提交领域装配（完整性校验）")
def submit_domain(
    body: DomainSubmitIn,
    actor: Actor = Depends(require_permissions("domain.assemble")),
    db: Session = Depends(get_db),
) -> dict:
    result = AdminService(db).submit_domain(body.config, actor)
    if result.get("validation") != "PASS":
        raise biz("VALIDATION_ERROR", "装配校验失败：" + "；".join(result.get("problems", [])), 400)
    return ok(result)


@router.get("/domains", summary="领域实例列表")
def list_domains(
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(AdminService(db).list_domains())


@router.get("/domains/{domain_id}", summary="领域装配配置与版本")
def get_domain(
    domain_id: str,
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).get_domain(domain_id))
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc


@router.post("/domains/{domain_id}/publish", summary="上线（MVP 两态直发）")
def publish_domain(
    domain_id: str,
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).publish_domain(domain_id, actor))
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc


@router.post("/domains/{domain_id}/rollback", summary="回滚到上一版本")
def rollback_domain(
    domain_id: str,
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).rollback_domain(domain_id, actor))
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc
    except ValueError as exc:
        raise biz("STATE_INVALID", str(exc), 409) from exc


@router.post("/domains/{domain_id}/offline", summary="下线")
def offline_domain(
    domain_id: str,
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).offline_domain(domain_id, actor))
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc
