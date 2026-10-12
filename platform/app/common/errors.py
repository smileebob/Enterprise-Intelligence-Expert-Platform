"""统一异常与错误处理（规则 B-05 / C-04 / H-01）。

- BusinessError：业务异常（错误码 + HTTP 状态），由统一处理器转 JSON；
- 错误码集中成表维护；500 响应不泄漏堆栈/SQL；
- RequestValidationError 统一转 400 + 字段级错误。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.context import get_trace_id

logger = logging.getLogger(__name__)

# 错误码表（集中维护，规则 C-04）
ERROR_CODES: dict[str, tuple[int, str]] = {
    "VALIDATION_ERROR": (400, "参数校验失败"),
    "AUTH_INVALID": (401, "认证失败"),
    "AUTH_FAILED": (401, "用户名或密码错误"),
    "PERM_DENIED": (403, "无权限"),
    "NOT_FOUND": (404, "资源不存在"),
    "SESSION_NOT_FOUND": (404, "会话不存在"),
    "MESSAGE_NOT_FOUND": (404, "消息不存在"),
    "DOCUMENT_NOT_FOUND": (404, "文档任务不存在"),
    "CONFLICT_NOT_FOUND": (404, "冲突记录不存在"),
    "EVAL_NOT_FOUND": (404, "评估结果不存在"),
    "DOMAIN_NOT_FOUND": (404, "领域实例不存在"),
    "MEMORY_NOT_FOUND": (404, "记忆条目不存在"),
    "ROLE_NOT_FOUND": (404, "角色不存在"),
    "STANDARD_NOT_FOUND": (404, "领域标准不存在"),
    "CONFLICT_UNRESOLVED": (409, "存在冲突，建议人工核验"),
    "IDEMPOTENT_DUPLICATE": (409, "重复请求，请勿重复提交"),
    "STATE_INVALID": (409, "当前状态不允许该操作"),
    "RATE_LIMITED": (429, "请求过于频繁"),
    "INJECTION_DETECTED": (400, "输入包含不安全内容，已拦截"),
    "TOOL_CONFIRM_REQUIRED": (409, "危险操作需要二次确认"),
    "LLM_UNAVAILABLE": (503, "AI 分析暂不可用，已降级"),
    "INTERNAL_ERROR": (500, "系统内部错误"),
}


class BusinessError(Exception):
    """业务异常：携带错误码，统一由处理器转响应。"""

    def __init__(self, code: str, message: str | None = None, http_status: int | None = None):
        default_status, default_message = ERROR_CODES.get(code, (400, code))
        self.code = code
        self.message = message or default_message
        self.http_status = http_status or default_status
        super().__init__(self.message)


def biz(code: str, message: str | None = None, http_status: int | None = None) -> BusinessError:
    return BusinessError(code, message, http_status)


def _json(status_code: int, code: str, message: str, data: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        logger.info("business error: %s %s", exc.code, exc.message,
                    extra={"action": "business_error", "object": request.url.path})
        return _json(exc.http_status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        return _json(400, "VALIDATION_ERROR", detail or "参数校验失败",
                     {"errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]})

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return _json(429, "RATE_LIMITED", "请求过于频繁，请稍后重试")

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code_map = {401: "AUTH_INVALID", 403: "PERM_DENIED", 404: "NOT_FOUND", 405: "NOT_FOUND"}
        code = code_map.get(exc.status_code, "INTERNAL_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else ERROR_CODES.get(code, (exc.status_code, "请求失败"))[1]
        return _json(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error: %s %s trace_id=%s", request.method, request.url.path, get_trace_id())
        return _json(500, "INTERNAL_ERROR", "系统内部错误")
