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
    "POLICY_NOT_FOUND": (404, "政策文件不存在"),
    "POLICY_VERSION_NOT_FOUND": (404, "政策版本不存在"),
    "POLICY_VERSION_IMMUTABLE": (409, "已审核政策版本不可原地修改"),
    "POLICY_RELATION_NOT_FOUND": (404, "政策版本关系不存在"),
    "POLICY_RELATION_TARGET_MISSING": (409, "政策版本关系目标不存在"),
    "POLICY_CLAUSE_NOT_FOUND": (404, "政策条款不存在或不可访问"),
    "DOCUMENT_DRAFT_NOT_FOUND": (404, "公文草稿不存在"),
    "DOCUMENT_DRAFT_IMMUTABLE": (409, "已审核公文不可原地修改"),
    "DOCUMENT_DRAFT_CONFLICT": (409, "公文草稿版本冲突"),
    "DOCUMENT_REVIEW_STATE_INVALID": (409, "公文草稿状态不允许审核"),
    "DOCUMENT_REVIEW_SEPARATION": (409, "起草人不能审核自己的草稿"),
    "DOCUMENT_EXPORT_NOT_APPROVED": (409, "公文尚未审核通过"),
    "DOCUMENT_EXPORT_BLOCKED": (409, "公文存在阻断项或敏感信息"),
    "INTERPRET_SESSION_NOT_FOUND": (404, "政策解读会话不存在"),
    "WRITING_DRAFT_NOT_FOUND": (404, "文稿草稿不存在"),
    "WRITING_DRAFT_IMMUTABLE": (409, "已审核文稿不可原地修改"),
    "WRITING_DRAFT_CONFLICT": (409, "文稿草稿版本冲突"),
    "POLICY_VERSION_CONFLICT": (409, "政策版本冲突"),
    "POLICY_DOCUMENT_CONFLICT": (409, "政策文件元数据冲突"),
    "POLICY_SOURCE_HASH_MISMATCH": (400, "政策正文哈希不匹配"),
    "POLICY_CLAUSE_DUPLICATE": (400, "政策条款编号重复"),
    "POLICY_REVIEW_STATE_INVALID": (409, "政策审核状态不允许该操作"),
    "POLICY_PUBLISH_NO_APPROVED": (409, "没有已审核版本可发布"),
    "EMBEDDING_RESULT_INVALID": (503, "向量生成结果不完整"),
    "EVAL_NOT_FOUND": (404, "评估结果不存在"),
    "DOMAIN_NOT_FOUND": (404, "领域实例不存在"),
    "DOMAIN_FORBIDDEN": (403, "无权访问该领域"),
    "DOMAIN_OFFLINE": (409, "领域未上线"),
    "INVALID_FEEDBACK_TARGET": (400, "反馈目标不合法"),
    "FEEDBACK_NOT_FOUND": (404, "反馈不存在"),
    "PROMOTION_REJECTED": (409, "晋升条件不满足"),
    "MEMORY_NOT_FOUND": (404, "记忆条目不存在"),
    "RUN_NOT_FOUND": (404, "运行不存在或无权查看"),
    "APPROVAL_NOT_FOUND": (404, "审批不存在"),
    "APPROVAL_CONFLICT": (409, "审批状态冲突"),
    "APPROVAL_EXPIRED": (410, "审批已过期"),
    "RULE_NOT_FOUND": (404, "规则不存在"),
    "MCP_DISABLED": (404, "MCP 接入未启用"),
    "REPLAY_UNAVAILABLE": (409, "回放所需快照不可用"),
    "REPLAY_VERSION_MISMATCH": (409, "回放代码版本不匹配"),
    "FORK_UNAVAILABLE": (409, "运行不可分叉或缺少快照"),
    "MCP_CLIENT_UNAVAILABLE": (409, "外部 MCP 服务不可用或未授权"),
    "MCP_CLIENT_REJECTED": (400, "外部 MCP 地址或参数不安全"),
    "OUTPUT_REVIEW_REQUIRED": (409, "输出待人工审核"),
    "OUTPUT_REVIEW_NOT_FOUND": (404, "输出审核不存在"),
    "OUTPUT_REVIEW_CONFLICT": (409, "输出审核状态冲突"),
    "ROLE_NOT_FOUND": (404, "角色不存在"),
    "STANDARD_NOT_FOUND": (404, "领域标准不存在"),
    "CONFLICT_UNRESOLVED": (409, "存在冲突，建议人工核验"),
    "IDEMPOTENT_DUPLICATE": (409, "重复请求，请勿重复提交"),
    "STATE_INVALID": (409, "当前状态不允许该操作"),
    "PRECONDITION_REQUIRED": (428, "缺少版本前置条件"),
    "PRECONDITION_FAILED": (412, "版本前置条件不满足"),
    "IDEMPOTENCY_REQUIRED": (400, "缺少幂等键"),
    "IDEMPOTENCY_CONFLICT": (409, "幂等键对应不同请求内容"),
    "OPERATION_IN_PROGRESS": (409, "相同幂等键的请求正在处理中"),
    "API_UPGRADE_REQUIRED": (409, "接口已升级，请使用新版本"),
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
