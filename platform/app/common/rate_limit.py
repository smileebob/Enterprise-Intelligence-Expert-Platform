"""限流器（slowapi，规则 I-04）：全局默认限流 + 敏感接口加严。

存储：开发 memory，生产经 REDIS_URL 使用 Valkey/Redis（多实例一致）。
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

_settings = get_settings()
_storage_uri = _settings.REDIS_URL if _settings.CACHE_BACKEND == "redis" else "memory://"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[item.strip() for item in _settings.RATE_LIMIT_DEFAULT.split(",") if item.strip()],
    storage_uri=_storage_uri,
    config_filename=".env.slowapi-disabled",  # 不读 .env（避免 Windows GBK 解码问题；限流参数经 pydantic-settings 注入）
)


def login_limit_rule() -> str:
    return _settings.RATE_LIMIT_LOGIN
