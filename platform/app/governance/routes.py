"""治理域：角色权限管理（FR-9-04）、审计查询、工具授权矩阵查看。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.common.responses import ok
from app.db import get_db
from app.deps import require_permissions
from core.abstraction.dtos import Actor
from core.admin.service import AdminService
from core.gateway.permissions import role_permissions
from core.gateway.registry import tool_registry

router = APIRouter(tags=["governance"])


class RolePermissionsIn(BaseModel):
    permissions: list[str] = Field(min_length=0)


@router.get("/admin/roles", summary="角色与授权矩阵")
def list_roles(
    actor: Actor = Depends(require_permissions("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(AdminService(db).list_roles())


@router.get("/admin/permissions", summary="权限点清单")
def list_permissions(
    actor: Actor = Depends(require_permissions("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    return ok(AdminService(db).list_permissions())


@router.put("/admin/roles/{role_id}/permissions", summary="调整授权矩阵（全部变更写审计）")
def update_role_permissions(
    role_id: str,
    body: RolePermissionsIn,
    actor: Actor = Depends(require_permissions("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return ok(AdminService(db).update_role_permissions(role_id, body.permissions, actor))
    except KeyError as exc:
        raise biz("ROLE_NOT_FOUND", "角色不存在", 404) from exc
    except ValueError as exc:
        raise biz("VALIDATION_ERROR", str(exc), 400) from exc


@router.get("/admin/tools", summary="工具注册表与授权（角色 × 领域 × 工具）")
def list_tools(
    domain_id: str = "chemical",
    actor: Actor = Depends(require_permissions("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    tools = [
        {
            "name": definition.spec.name,
            "description": definition.spec.description,
            "permission": definition.spec.permission,
            "risk_level": definition.spec.risk_level,
            "domain": definition.spec.domain,
            "input_schema": definition.spec.input_schema,
            "rate_limit": definition.spec.rate_limit,
        }
        for definition in tool_registry.all()
    ]
    roles = ["domain_expert", "kb_admin", "platform_admin", "sec_auditor", "agent_service"]
    matrix = {role: sorted(role_permissions(db, role)) for role in roles}
    return ok({"tools": tools, "role_permissions": matrix})


@router.get("/audit/logs", summary="审计查询（安全审计员）")
def query_audit(
    trace_id: str | None = None,
    hook_type: str | None = None,
    actor_id: str | None = None,
    ts_from: str | None = None,
    ts_to: str | None = None,
    limit: int = 50,
    actor: Actor = Depends(require_permissions("audit.read")),
    db: Session = Depends(get_db),
) -> dict:
    rows = AdminService(db).query_audit(
        trace_id=trace_id,
        hook_type=hook_type,
        actor_id=actor_id,
        ts_from=ts_from,
        ts_to=ts_to,
        limit=min(max(limit, 1), 200),
    )
    return ok(rows)
