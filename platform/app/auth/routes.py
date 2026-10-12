"""认证域：登录（JWT 占位实现，对接企业统一身份可替换）+ 当前用户信息。

注意：本模块不使用 `from __future__ import annotations`（slowapi 装饰器包装后
注解字符串无法在其 globals 中解析，会导致 OpenAPI 生成失败）。
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.services import authenticate, create_access_token
from app.common.errors import biz
from app.common.rate_limit import limiter, login_limit_rule
from app.common.responses import ok
from app.db import get_db
from app.deps import get_current_actor
from core.abstraction.dtos import Actor
from core.gateway.permissions import role_permissions

router = APIRouter(tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: dict


@router.post("/auth/login", summary="登录获取 access token")
@limiter.limit(login_limit_rule())
def login(request: Request, body: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = authenticate(db, body.username, body.password)
    if user is None:
        raise biz("AUTH_FAILED", "用户名或密码错误", 401)
    token, expires_in = create_access_token(user)
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "name": user.name,
                "role_id": user.role_id,
                "org": user.org,
                "domain_scope": list(user.domain_scope or []),
            },
        }
    )


@router.get("/auth/me", summary="当前用户与权限")
def me(actor: Actor = Depends(get_current_actor), db: Session = Depends(get_db)) -> dict:
    return ok(
        {
            "user_id": actor.user_id,
            "username": actor.username,
            "role_id": actor.role_id,
            "permission_max": actor.permission_max,
            "domain_scope": actor.domain_scope,
            "permissions": sorted(role_permissions(db, actor.role_id)),
        }
    )
