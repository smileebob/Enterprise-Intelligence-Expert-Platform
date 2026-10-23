"""上下文预算与工具输出投影（04）：超限结果落受控载荷，模型/消费者只拿有界视图。"""

from __future__ import annotations

import json
from typing import Any

MAX_TOOL_ITEMS = 5
MAX_STRING_CHARS = 500
MAX_DICT_KEYS = 20


def _json_size(data: Any) -> int:
    return len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))


def _truncate_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_STRING_CHARS]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_truncate_value(item) for item in value[:3]]
    if isinstance(value, dict):
        return {key: _truncate_value(item) for key, item in list(value.items())[:MAX_DICT_KEYS]}
    return str(value)[:MAX_STRING_CHARS]


def project_tool_data(data: Any, *, inline_bytes: int) -> tuple[Any, dict[str, Any]]:
    """返回 (可消费视图, 投影元数据)；不改变小结果，超限时按类型结构化裁剪。"""
    size = _json_size(data)
    if size <= inline_bytes:
        return data, {"truncated": False, "original_bytes": size}
    view: Any
    if isinstance(data, list):
        view = [_truncate_value(item) for item in data[:MAX_TOOL_ITEMS]]
        total = len(data)
    elif isinstance(data, dict):
        view = {key: _truncate_value(value) for key, value in list(data.items())[:MAX_DICT_KEYS]}
        total = len(data)
    elif isinstance(data, str):
        view = data[: MAX_STRING_CHARS * 2]
        total = len(data)
    else:
        view = _truncate_value(data)
        total = None
    return view, {"truncated": True, "original_bytes": size, "total_count": total}


def trim_history(messages: list[dict[str, str]], max_bytes: int) -> list[dict[str, str]]:
    """从最近消息向前装入预算；单条超预算时截断正文，保证硬上限。"""
    kept: list[dict[str, str]] = []
    total = 0
    for message in reversed(messages):
        content = str(message.get("content") or "")
        size = len(content.encode("utf-8"))
        if size > max_bytes:
            content = content[: max(1, max_bytes // 4)]
            size = len(content.encode("utf-8"))
        if kept and total + size > max_bytes:
            break
        kept.append({"role": str(message.get("role") or ""), "content": content})
        total += size
    return list(reversed(kept))
