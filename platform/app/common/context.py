"""请求上下文（trace_id 贯穿，规则 H-02 / J-02）。"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(value: str) -> Token[str]:
    return _trace_id.set(value)


def reset_trace_id(token: Token[str]) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str:
    return _trace_id.get()
