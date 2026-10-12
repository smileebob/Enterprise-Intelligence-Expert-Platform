"""HTTP 中间件：trace_id 贯穿、访问日志、安全响应头（规则 J-02 / H-02 / I-07）。"""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.context import new_trace_id, reset_trace_id, set_trace_id

logger = logging.getLogger("app.access")


class TraceMiddleware:
    """纯 ASGI 中间件：生成/透传 X-Request-ID，写访问日志。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        trace_id = request.headers.get("X-Request-ID") or new_trace_id()
        token = set_trace_id(trace_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "%s %s -> %s (%sms)",
                request.method,
                request.url.path,
                status_holder["status"],
                duration_ms,
                extra={
                    "action": "http_request",
                    "object": request.url.path,
                    "result": status_holder["status"],
                    "duration_ms": duration_ms,
                },
            )
            reset_trace_id(token)


class SecurityHeadersMiddleware:
    """安全响应头（规则 I-07）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                if scope.get("scheme") == "https":
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
