"""结构化日志（规则 J-01）：生产 JSON（python-json-logger），开发可读文本。

日志字段：ts / level / logger / message / request_id（trace_id）。
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from app.common.context import get_trace_id


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_trace_id()
        return True


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())
    if fmt == "json":
        formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | [%(request_id)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 降低三方库噪声
    for noisy in ("httpcore", "httpx", "urllib3", "jieba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
