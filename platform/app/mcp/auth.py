"""MCP Bearer 中间件（受控客户端）：验证 audience/type/exp 后写入 ASGI user。"""

from __future__ import annotations

import json

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.authentication import AuthCredentials
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp.tokens import verify_mcp_token


class McpBearerMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        origin = headers.get("origin")
        from app.config import get_settings

        settings = get_settings()
        if origin and origin not in settings.MCP_ALLOWED_ORIGINS:
            await self._reject(send, 403, "origin_not_allowed")
            return
        authorization = headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            await self._reject(send, 401, "bearer_required")
            return
        payload = verify_mcp_token(authorization[7:].strip())
        if payload is None:
            await self._reject(send, 401, "invalid_token")
            return
        access_token = AccessToken(
            token=authorization[7:].strip(),
            client_id=str(payload.get("sub") or ""),
            scopes=str(payload.get("scope") or "").split(),
            expires_at=int(payload.get("exp") or 0),
            resource=str(payload.get("aud") or ""),
            subject=str(payload.get("sub") or ""),
            claims=payload,
        )
        scope["user"] = AuthenticatedUser(access_token)
        scope["auth"] = AuthCredentials(access_token.scopes)
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send, status: int, code: str) -> None:
        body = json.dumps({"error": code}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer error="invalid_token"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
