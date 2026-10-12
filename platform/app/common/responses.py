"""统一响应包装（规则 B-05 / C-04）：{code, message, data}。"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    return {"code": "SUCCESS", "message": message, "data": data}


def fail(code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def page(items: list[Any], next_cursor: str | None, page_size: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "items": items,
        "next_cursor": next_cursor,
        "page_size": page_size,
    }
    if extra:
        body.update(extra)
    return body
