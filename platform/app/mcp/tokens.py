"""MCP 专用短期 bearer（受控客户端接入；不是任意 host 的 OAuth 兼容凭证）。"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt

from app.config import get_settings
from core.abstraction.dtos import Actor


def create_mcp_token(actor: Actor) -> tuple[str, int, str]:
    settings = get_settings()
    ttl = max(60, int(settings.MCP_TOKEN_TTL_SECONDS))
    now = int(time.time())
    payload = {
        "sub": actor.user_id,
        "aud": settings.MCP_RESOURCE_URL,
        "iat": now,
        "exp": now + ttl,
        "typ": "mcp_access",
        "scope": "mcp.read",
        "jti": uuid.uuid4().hex,
        "role_id": actor.role_id,
        "domain_scope": list(actor.domain_scope or []),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, ttl, settings.MCP_RESOURCE_URL


def verify_mcp_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.MCP_RESOURCE_URL,
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "mcp_access":
        return None
    scopes = str(payload.get("scope") or "").split()
    if "mcp.read" not in scopes:
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        return None
    return payload
