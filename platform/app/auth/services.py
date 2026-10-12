"""认证域服务：JWT 签发/校验（I-03/I-06；MVP 占位实现，对接企业统一身份可替换）。

密码哈希实现已下沉 `core.admin.security`（底座共用，core 不再反向依赖 app）；此处保留同名再导出兼容既有导入。
"""

from __future__ import annotations

from datetime import timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import biz
from app.config import get_settings
from core.admin.models import GovUser
from core.admin.security import hash_password, verify_password  # noqa: F401 - 再导出兼容既有导入路径
from core.clock import utc_now


def create_access_token(user: GovUser) -> tuple[str, int]:
    settings = get_settings()
    expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user.user_id,
        "role": user.role_id,
        "exp": utc_now() + expires,
        "iat": utc_now(),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires.total_seconds())


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise biz("AUTH_INVALID", "认证失败，请重新登录", 401) from exc


def authenticate(db: Session, username: str, password: str) -> GovUser | None:
    user = db.execute(select(GovUser).where(GovUser.username == username)).scalars().first()
    if user is None or user.status != "active":
        # 登录失败不区分「用户不存在/密码错误」（I-06）
        verify_password(hash_password("dummy"), password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user
