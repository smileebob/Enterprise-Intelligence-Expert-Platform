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
from app.db import get_db
from app.deps import require_permissions
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
    value: Any = None


class FeedbackIn(BaseModel):
    rating: FeedbackRating
    correction: str = Field(default="", max_length=2000)
    comment: str = Field(default="", max_length=2000)


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
    from core.runtime import get_domain_registry

    plugin = get_domain_registry().get(body.domain_id)
    if plugin is None:
        raise biz("DOMAIN_NOT_FOUND", f"领域实例未装配: {body.domain_id}", 404)
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

    # ① 输入 Hook（路由依赖层）：注入检测拦截 / PII 脱敏 / 审计
    hook_ctx = HookContext(stage=HookStage.INPUT, actor=actor, domain_id=session.domain_id, session_id=session_id)
    decision = InputGuard(db).check(body.text, hook_ctx)
    if decision.blocked:
        raise BusinessError(decision.code, decision.message, 400)
    masked_text = decision.payload.get("text", body.text)

    user_message = store.add_message(session_id, "user", masked_text)
    orchestrator = Orchestrator(db)

    async def runner(emit):
        return await orchestrator.run_turn(
            session, masked_text, actor, message_id=user_message.message_id, emit=emit
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
    store.add_message(session_id, "user", str(body.value))
    orchestrator = Orchestrator(db)

    async def runner(emit):
        return await orchestrator.resume(session_id, body.value, actor, emit=emit)

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
    domain_id = session.domain_id if session else "chemical"

    result = GrowthService(db).record_feedback(
        user_id=actor.user_id,
        domain_id=domain_id,
        rating=body.rating,
        session_id=message.session_id,
        message_id=message_id,
        correction=body.correction,
        comment=body.comment,
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
    elif body.correction:
        content = f"用户修正意见：{body.correction[:200]}"
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
