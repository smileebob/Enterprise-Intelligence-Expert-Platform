"""对话域：会话管理、SSE 流式消息（先校验后外发）、澄清恢复、反馈回流。

SSE 事件：step / citation / token / eval / clarify / done / error（05 §5.2）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.errors import BusinessError, biz
from app.common.responses import ok
from app.config import get_settings
from app.db import get_db
from app.deps import assert_domain_access, require_permissions
from core.abstraction.dtos import Actor, MemoryCandidate
from core.abstraction.enums import FeedbackRating, HookStage, MemoryLayer
from core.abstraction.errors import ToolDeniedError
from core.growth.service import GrowthService
from core.guardrails.hooks import HookContext
from core.guardrails.input_hook import InputGuard
from core.memory.service import MemoryService
from core.orchestration.engine import Orchestrator
from core.orchestration.session import ChatSessionStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class SessionIn(BaseModel):
    domain_id: str = Field(default="chemical", max_length=64)
    title: str = Field(default="", max_length=120)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ResumeIn(BaseModel):
    """澄清/确认回复：有界文本 + 可选审批 ID（不接受任意 JSON 写入）。"""

    value: str = Field(default="", max_length=4000)
    approval_id: str | None = Field(default=None, max_length=64)
    review_id: str | None = Field(default=None, max_length=64)


class FeedbackIn(BaseModel):
    rating: FeedbackRating
    correction: str = Field(default="", max_length=2000)
    comment: str = Field(default="", max_length=2000)


def _guarded_input(db: Session, text: str, ctx: HookContext) -> str:
    """入口文本统一审核：注入拦截（400）+ PII 脱敏；空文本原样返回。"""
    if not text:
        return text
    decision = InputGuard(db).check(text, ctx)
    if decision.blocked:
        raise BusinessError(decision.code, decision.message, 400)
    return decision.payload.get("text", text)


def _start_run(db: Session, session, actor: Actor):
    """为回合创建采集运行（run.started + manifest）；失败不影响既有业务表。"""
    from core.admin.models import DomInstance
    from core.runs.service import RunManifest, RunService
    from core.runtime import get_db_domain_registry

    settings = get_settings()
    plugin = get_db_domain_registry(db).get(session.domain_id)
    hooks = getattr(plugin, "hooks", None) if plugin is not None else None
    instance = db.get(DomInstance, session.domain_id)
    manifest = RunManifest(
        code_version=getattr(hooks, "CODE_VERSION", "") if hooks is not None else "",
        plugin_revision=getattr(instance, "config_rev", 0) or 0,
        config_rev=getattr(instance, "config_rev", 0) or 0,
        model_mode=settings.DEMO_MODE,
        model_alias=settings.LLM_MODEL if settings.DEMO_MODE == "live" else "offline-rule",
        event_schema_version=settings.RUN_EVENT_SCHEMA_VERSION,
    )
    return RunService(db).create_run(
        session_id=session.session_id,
        owner_id=actor.user_id,
        domain_id=session.domain_id,
        manifest=manifest,
    )


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_response(orchestrator: Orchestrator, runner) -> StreamingResponse:
    """把编排事件队列转成 SSE 流（过程帧先行，正文 token 在输出 Hook 后开流）。"""

    async def event_stream():
        import time as _time

        from core.metrics import CHAT_TTFT

        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()
        started = _time.perf_counter()
        ttft_recorded = False

        async def emit(kind: str, data: dict[str, Any]) -> None:
            await queue.put((kind, data))

        async def wrapped():
            try:
                return await runner(emit)
            except BusinessError as exc:
                await queue.put(("error", {"code": exc.code, "message": exc.message}))
                return None
            except ToolDeniedError as exc:
                await queue.put(("error", {"code": exc.code, "message": str(exc)}))
                return None
            except Exception:
                logger.exception("turn failed")
                await queue.put(("error", {"code": "INTERNAL_ERROR", "message": "系统内部错误"}))
                return None
            finally:
                await queue.put(sentinel)

        task = asyncio.create_task(wrapped())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                kind, data = item
                if kind == "token" and not ttft_recorded:
                    ttft_recorded = True
                    CHAT_TTFT.observe(_time.perf_counter() - started)
                yield _sse_frame(kind, data)
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/chat/sessions", summary="创建对话会话")
def create_session(
    body: SessionIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
) -> dict:
    from core.runtime import get_db_domain_registry

    assert_domain_access(actor, body.domain_id, db=db)
    plugin = get_db_domain_registry(db).get(body.domain_id)
    store = ChatSessionStore(db)
    session = store.create_session(body.domain_id, actor.user_id, title=body.title or f"{plugin.name}会话")
    return ok({"session_id": session.session_id, "domain_id": session.domain_id, "title": session.title})


@router.get("/chat/sessions", summary="我的会话列表")
def list_sessions(
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
    limit: int = 20,
) -> dict:
    rows = ChatSessionStore(db).list_sessions(actor.user_id, limit)
    return ok(
        [
            {
                "session_id": r.session_id,
                "domain_id": r.domain_id,
                "title": r.title,
                "status": r.status,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    )


@router.post("/chat/sessions/{session_id}/messages", summary="发送消息（SSE 流式响应）")
def send_message(
    session_id: str,
    request: Request,
    body: MessageIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
):
    store = ChatSessionStore(db)
    session = store.get_session(session_id)
    if session is None or session.user_id != actor.user_id:
        raise biz("SESSION_NOT_FOUND", "会话不存在", 404)
    assert_domain_access(actor, session.domain_id, db=db)

    # ① 输入 Hook（路由依赖层）：注入检测拦截 / PII 脱敏 / 审计
    hook_ctx = HookContext(stage=HookStage.INPUT, actor=actor, domain_id=session.domain_id, session_id=session_id)
    masked_text = _guarded_input(db, body.text, hook_ctx)

    user_message = store.add_message(session_id, "user", masked_text)
    run = _start_run(db, session, actor)
    orchestrator = Orchestrator(db)

    async def runner(emit):
        return await orchestrator.run_turn(
            session,
            masked_text,
            actor,
            message_id=user_message.message_id,
            emit=emit,
            run_id=run.run_id,
        )

    return _stream_response(orchestrator, runner)


@router.post("/chat/sessions/{session_id}/resume", summary="澄清/确认恢复（interrupt → resume，SSE）")
def resume_session(
    session_id: str,
    request: Request,
    body: ResumeIn,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
):
    store = ChatSessionStore(db)
    session = store.get_session(session_id)
    if session is None or session.user_id != actor.user_id:
        raise biz("SESSION_NOT_FOUND", "会话不存在", 404)
    assert_domain_access(actor, session.domain_id, db=db)

    if not body.value.strip() and not body.approval_id and not body.review_id:
        raise biz("VALIDATION_ERROR", "恢复请求需提供文本、审批 ID 或审核 ID", 400)

    # 恢复回复与首轮消息同等的输入审核（注入拦截 + PII 脱敏）后才落库
    hook_ctx = HookContext(stage=HookStage.INPUT, actor=actor, domain_id=session.domain_id, session_id=session_id)
    masked_value = _guarded_input(db, body.value, hook_ctx) if body.value else ""

    store.add_message(session_id, "user", masked_value or f"[审批 {body.approval_id} 恢复执行]")
    from core.runs.service import RunService

    runs = RunService(db)
    waiting_review = runs.latest_run_for_session(session_id, status="waiting_review")
    if waiting_review is not None and not body.review_id:
        raise biz("OUTPUT_REVIEW_REQUIRED", "输出待审核，请先批准审核记录", 409)
    waiting = waiting_review or runs.latest_run_for_session(session_id, status="waiting_approval")
    if waiting is None:
        waiting = runs.latest_run_for_session(session_id, status="waiting_input")
    run = waiting if waiting is not None else _start_run(db, session, actor)
    orchestrator = Orchestrator(db)

    async def runner(emit):
        return await orchestrator.resume(
            session_id,
            masked_value,
            actor,
            emit=emit,
            run_id=run.run_id,
            approval_id=body.approval_id or "",
            review_id=body.review_id or "",
        )

    return _stream_response(orchestrator, runner)


@router.get("/chat/sessions/{session_id}/messages", summary="历史消息（游标分页）")
def list_messages(
    session_id: str,
    cursor: str | None = None,
    page_size: int = 20,
    actor: Actor = Depends(require_permissions("chat.use")),
    db: Session = Depends(get_db),
) -> dict:
    store = ChatSessionStore(db)
    session = store.get_session(session_id)
    if session is None or session.user_id != actor.user_id:
        raise biz("SESSION_NOT_FOUND", "会话不存在", 404)
    messages, next_cursor = store.list_messages(session_id, cursor=cursor, page_size=page_size)
    items = [
        {
            "message_id": m.message_id,
            "role": m.role,
            "content": m.content,
            "citations": m.citations or [],
            "conflicts": m.conflicts or [],
            "eval_ref": m.eval_ref,
            "degrade_level": m.degrade_level,
            "feedback": m.feedback,
            "trace_id": m.trace_id,
            "meta": m.meta or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in reversed(messages)  # 时间正序返回
    ]
    return ok({"items": items, "next_cursor": next_cursor, "page_size": page_size})


@router.post("/chat/messages/{message_id}/feedback", summary="采纳/修正/否决（回流自成长 + 记忆写入）")
async def message_feedback(
    message_id: str,
    body: FeedbackIn,
    actor: Actor = Depends(require_permissions("growth.feedback")),
    db: Session = Depends(get_db),
) -> dict:
    store = ChatSessionStore(db)
    message = store.get_message(message_id)
    if message is None:
        raise biz("MESSAGE_NOT_FOUND", "消息不存在", 404)
    session = store.get_session(message.session_id)
    if session is None or session.user_id != actor.user_id:
        raise biz("SESSION_NOT_FOUND", "会话不存在", 404)
    assert_domain_access(actor, session.domain_id, db=db)
    if message.role != "assistant":
        raise biz("INVALID_FEEDBACK_TARGET", "仅可对助手回复提交反馈", 400)
    domain_id = session.domain_id

    hook_ctx = HookContext(
        stage=HookStage.INPUT,
        actor=actor,
        domain_id=domain_id,
        session_id=message.session_id,
        trace_id=message.trace_id or "",
    )
    correction = _guarded_input(db, body.correction, hook_ctx)
    comment = _guarded_input(db, body.comment, hook_ctx)

    result = GrowthService(db).record_feedback(
        user_id=actor.user_id,
        domain_id=domain_id,
        rating=body.rating,
        session_id=message.session_id,
        message_id=message_id,
        correction=correction,
        comment=comment,
        trace_id=message.trace_id,
        message_content=message.content if body.rating == FeedbackRating.ADOPTED else "",
        citations=message.citations,
        actor=actor,
    )
    store.set_feedback(message_id, str(body.rating))
    from core.metrics import SOLUTION_FEEDBACK

    SOLUTION_FEEDBACK.labels(rating=str(body.rating)).inc()

    # ⑤ 记忆写入 Hook：用户级记忆（偏好/修正沉淀）
    content = None
    if body.rating == FeedbackRating.ADOPTED:
        content = f"用户采纳方案：{message.content[:120]}"
    elif correction:
        content = f"用户修正意见：{correction[:200]}"
    if content:
        memory = MemoryService(db)
        write_ctx = HookContext(
            stage=HookStage.MEMORY_WRITE,
            actor=actor,
            domain_id=domain_id,
            session_id=message.session_id,
            trace_id=message.trace_id or "",
        )
        await memory.write(
            MemoryCandidate(
                layer=MemoryLayer.USER,
                owner_id=actor.user_id,
                content=content,
                domain_id=domain_id,
                source_type="stated",
                confidence=0.75,
                origin="feedback",
            ),
            write_ctx,
        )
    return ok(result)
