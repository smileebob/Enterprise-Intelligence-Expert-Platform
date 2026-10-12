"""会话与消息存储：会话生命周期、游标分页历史、反馈落库（会话级记忆=消息滑窗）。"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from core.orchestration.models import ChatMessage, ChatSession


def _encode_cursor(message: ChatMessage) -> str:
    raw = f"{message.created_at.isoformat()}|{message.message_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts, message_id = raw.split("|", 1)
        return ts, message_id
    except Exception:
        return None


class ChatSessionStore:
    def __init__(self, db: Session):
        self.db = db

    # ---------------- 会话 ----------------
    def create_session(self, domain_id: str, user_id: str, title: str = "", meta: dict[str, Any] | None = None) -> ChatSession:
        session = ChatSession(
            session_id=f"S-{uuid.uuid4().hex[:16]}",
            domain_id=domain_id,
            user_id=user_id,
            title=title or "新会话",
            meta=meta or {},
        )
        self.db.add(session)
        self.db.commit()
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self.db.get(ChatSession, session_id)

    def set_status(self, session_id: str, status: str) -> None:
        session = self.db.get(ChatSession, session_id)
        if session is not None:
            session.status = status
            self.db.commit()

    # ---------------- 消息 ----------------
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        citations: list[dict[str, Any]] | None = None,
        conflicts: list[dict[str, Any]] | None = None,
        eval_ref: str | None = None,
        degrade_level: str = "L0",
        trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            message_id=f"MSG-{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            role=role,
            content=content,
            citations=citations,
            conflicts=conflicts,
            eval_ref=eval_ref,
            degrade_level=degrade_level,
            trace_id=trace_id,
            meta=meta or {},
        )
        self.db.add(message)
        self.db.commit()
        return message

    def get_message(self, message_id: str) -> ChatMessage | None:
        return self.db.get(ChatMessage, message_id)

    def list_messages(self, session_id: str, cursor: str | None = None, page_size: int = 20) -> tuple[list[ChatMessage], str | None]:
        page_size = max(1, min(page_size, 100))
        stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
        if cursor:
            decoded = _decode_cursor(cursor)
            if decoded:
                ts, message_id = decoded
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at < ts,
                        and_(ChatMessage.created_at == ts, ChatMessage.message_id < message_id),
                    )
                )
        rows = self.db.execute(stmt.order_by(ChatMessage.created_at.desc(), ChatMessage.message_id.desc()).limit(page_size + 1)).scalars().all()
        has_more = len(rows) > page_size
        items = rows[:page_size]
        next_cursor = _encode_cursor(items[-1]) if has_more and items else None
        return items, next_cursor

    def recent_messages(self, session_id: str, limit: int = 10) -> list[ChatMessage]:
        rows = (
            self.db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(reversed(rows))

    def list_sessions(self, user_id: str, limit: int = 20) -> list[ChatSession]:
        rows = (
            self.db.execute(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc())
                .limit(max(1, min(limit, 100)))
            )
            .scalars()
            .all()
        )
        return list(rows)

    def set_feedback(self, message_id: str, rating: str) -> None:
        message = self.db.get(ChatMessage, message_id)
        if message is not None:
            message.feedback = rating
            self.db.commit()
