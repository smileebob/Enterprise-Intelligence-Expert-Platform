"""领域装配 v2（02 · revision/CAS/幂等）：写操作必须带 If-Match 与 Idempotency-Key。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import require_permissions
from core.abstraction.dtos import Actor
from core.admin.revisions import content_hash
from core.admin.service import (
    AdminService,
    IdempotencyConflict,
    OperationInProgress,
    PreconditionFailed,
)

router = APIRouter(tags=["domains-v2"])

IDEM_KEY_MAX = 128


class DomainSubmitV2(BaseModel):
    config: dict[str, Any] = Field(description="domain.yaml 定稿（五要素契约）")


class PublishIn(BaseModel):
    revision_id: str | None = Field(default=None, max_length=64)


class RollbackIn(BaseModel):
    revision_id: str = Field(min_length=1, max_length=64)


def _parse_if_match(value: str | None) -> int:
    if value is None or not value.strip():
        raise biz("PRECONDITION_REQUIRED", "写操作必须携带 If-Match（配置版本）", 428)
    raw = value.strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise biz("PRECONDITION_REQUIRED", "If-Match 必须为 config_rev 整数值", 428) from exc


def _idem_key(value: str | None) -> str:
    if not value:
        raise biz("IDEMPOTENCY_REQUIRED", "写操作必须携带 Idempotency-Key", 400)
    if len(value) > IDEM_KEY_MAX:
        raise biz("VALIDATION_ERROR", f"Idempotency-Key 长度不能超过 {IDEM_KEY_MAX}", 400)
    return value


def _headers(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idem: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> tuple[int, str]:
    return _parse_if_match(if_match), _idem_key(idem)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例或配置修订不存在", 404) from exc
    except PreconditionFailed as exc:
        raise biz("PRECONDITION_FAILED", str(exc), 412) from exc
    except IdempotencyConflict as exc:
        raise biz("IDEMPOTENCY_CONFLICT", str(exc), 409) from exc
    except OperationInProgress as exc:
        raise biz("OPERATION_IN_PROGRESS", str(exc), 409) from exc
    except ValueError as exc:
        raise biz("STATE_INVALID", str(exc), 409) from exc


@router.get("/domains", summary="领域实例列表（含 config_rev）")
def list_domains(
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(AdminService(db).list_domains())


@router.get("/domains/{domain_id}", summary="领域装配（ETag=config_rev）")
def get_domain(
    domain_id: str,
    response: Response,
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        data = AdminService(db).get_domain(domain_id)
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc
    response.headers["ETag"] = f'"{data["config_rev"]}"'
    return ok(data)


@router.get("/domains/{domain_id}/revisions", summary="配置修订历史（倒序）")
def list_revisions(
    domain_id: str,
    actor: Actor = Depends(require_permissions("domain.view")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).list_revisions(domain_id))
    except KeyError as exc:
        raise biz("DOMAIN_NOT_FOUND", "领域实例不存在", 404) from exc


@router.post("/domains", status_code=201, summary="创建领域草稿（Idempotency-Key 必填）")
def create_domain(
    body: DomainSubmitV2,
    idem: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    actor: Actor = Depends(require_permissions("domain.assemble")),
    db: Session = Depends(get_db),
) -> dict:
    key = _idem_key(idem)
    service = AdminService(db)
    result = _call(
        service.submit_domain,
        body.config,
        actor,
        expected_rev=0,
        idem_key=key,
        request_hash=content_hash({"op": "submit", "config": body.config}),
    )
    if result.get("validation") != "PASS":
        raise biz("VALIDATION_ERROR", "装配校验失败：" + "；".join(result.get("problems", [])), 400)
    return ok(result)


@router.put("/domains/{domain_id}/draft", summary="提交新草稿（If-Match + Idempotency-Key）")
def submit_draft(
    domain_id: str,
    body: DomainSubmitV2,
    headers: tuple[int, str] = Depends(_headers),
    actor: Actor = Depends(require_permissions("domain.assemble")),
    db: Session = Depends(get_db),
) -> dict:
    expected, key = headers
    config = {**body.config, "domain_id": domain_id}
    result = _call(
        AdminService(db).submit_domain,
        config,
        actor,
        expected_rev=expected,
        idem_key=key,
        request_hash=content_hash({"op": "submit", "config": config}),
    )
    if result.get("validation") != "PASS":
        raise biz("VALIDATION_ERROR", "装配校验失败：" + "；".join(result.get("problems", [])), 400)
    return ok(result)


@router.post("/domains/{domain_id}/publish", summary="上线（指定/使用草稿 revision）")
def publish_domain(
    domain_id: str,
    body: PublishIn,
    headers: tuple[int, str] = Depends(_headers),
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    expected, key = headers
    result = _call(
        AdminService(db).publish_domain,
        domain_id,
        actor,
        expected_rev=expected,
        revision_id=body.revision_id,
        idem_key=key,
        request_hash=content_hash({"op": "publish", "revision_id": body.revision_id or ""}),
    )
    return ok(result)


@router.post("/domains/{domain_id}/rollback", summary="回滚到指定历史 revision（产生更大 revision）")
def rollback_domain(
    domain_id: str,
    body: RollbackIn,
    headers: tuple[int, str] = Depends(_headers),
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    expected, key = headers
    result = _call(
        AdminService(db).rollback_domain,
        domain_id,
        actor,
        revision_id=body.revision_id,
        expected_rev=expected,
        idem_key=key,
        request_hash=content_hash({"op": "rollback", "revision_id": body.revision_id}),
    )
    return ok(result)


@router.post("/domains/{domain_id}/offline", summary="下线（产生新 revision）")
def offline_domain(
    domain_id: str,
    headers: tuple[int, str] = Depends(_headers),
    actor: Actor = Depends(require_permissions("domain.publish")),
    db: Session = Depends(get_db),
) -> dict:
    expected, key = headers
    result = _call(
        AdminService(db).offline_domain,
        domain_id,
        actor,
        expected_rev=expected,
        idem_key=key,
        request_hash=content_hash({"op": "offline"}),
    )
    return ok(result)
