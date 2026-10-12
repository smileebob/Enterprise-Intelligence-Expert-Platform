"""公共依赖：当前用户（JWT → Actor）、权限校验、幂等键、分页参数。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.common.errors import BusinessError, biz
from app.db import get_db
from core.abstraction.dtos import Actor
from core.admin.models import GovUser
from core.gateway.permissions import permission_max_for_role, role_permissions

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_current_actor(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    db: Session = Depends(get_db),
) -> Actor:
    if not token:
        raise biz("AUTH_INVALID", "未提供认证凭据", 401)
    from app.auth.services import decode_token

    payload = decode_token(token)
    user = db.get(GovUser, payload.get("sub", ""))
    if user is None or user.status != "active":
        raise biz("AUTH_INVALID", "账号不可用", 401)
    return Actor(
        user_id=user.user_id,
        username=user.username,
        role_id=user.role_id,
        permission_max=permission_max_for_role(user.role_id),
        domain_scope=list(user.domain_scope or []),
    )


def require_permissions(*required: str):
    """权限点依赖工厂：角色缺少任一权限点即 403。"""

    def checker(actor: Actor = Depends(get_current_actor), db: Session = Depends(get_db)) -> Actor:
        granted = role_permissions(db, actor.role_id)
        missing = [perm for perm in required if perm not in granted]
        if missing:
            raise BusinessError("PERM_DENIED", f"缺少权限：{'、'.join(missing)}", 403)
        return actor

    return checker


def idempotency_key(
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    return idempotency_key_header


class PageParams:
    def __init__(self, page: int = 1, page_size: int = 20):
        self.page = max(1, page)
        self.page_size = max(1, min(page_size, 100))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
