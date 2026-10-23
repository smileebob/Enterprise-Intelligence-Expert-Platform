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
        # 安全边界实时校验：跨进程撤权在下一请求立即生效，不依赖进程缓存 TTL
        granted = role_permissions(db, actor.role_id, fresh=True)
        missing = [perm for perm in required if perm not in granted]
        if missing:
            raise BusinessError("PERM_DENIED", f"缺少权限：{'、'.join(missing)}", 403)
        return actor

    return checker


def assert_domain_access(
    actor: Actor, domain_id: str, *, db: Session, require_online: bool = True
) -> None:
    """领域准入：主体域范围 → DB 实例存在 → DB 在线状态（跨进程权威，不用进程内插件状态）。"""
    from core.admin.models import DomInstance

    if domain_id not in (actor.domain_scope or []):
        raise BusinessError("DOMAIN_FORBIDDEN", "无权访问该领域", 403)
    row = db.get(DomInstance, domain_id)
    if row is None:
        raise BusinessError("DOMAIN_NOT_FOUND", "领域实例未装配", 404)
    if require_online and row.status != "online":
        raise BusinessError("DOMAIN_OFFLINE", "领域未上线", 409)


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
