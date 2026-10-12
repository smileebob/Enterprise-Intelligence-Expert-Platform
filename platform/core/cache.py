"""缓存与幂等锁统一封装（规则 E-01~E-05）—— 全平台唯一的缓存访问入口。

- 后端统一经 fastapi-cache2：开发 InMemoryBackend，生产 RedisBackend（Valkey 兼容）；
- Key 规范：`angentplatform:{module}:{entity}:{id}`（全部经 `ckey()` 构造）；
- 在 fastapi-cache2 后端上补齐 SETNX（add）与 delete，供幂等键/互斥锁使用；
- TTL 强制设置并支持随机抖动（防雪崩，规则 E-03）。
"""

from __future__ import annotations

import json
import random
from typing import Any

from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend, Value
from fastapi_cache.backends.redis import RedisBackend

from app.config import get_settings


class GuardedInMemoryBackend(InMemoryBackend):
    """补齐 add（SETNX）/ delete 的内存后端。"""

    async def add(self, key: str, value: bytes, expire: int | None = None) -> bool:
        async with self._lock:
            if self._get(key) is not None:
                return False
            self._store[key] = Value(value, self._now + (expire or 0))
            return True

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)


def init_cache(settings) -> None:
    """缓存后端集中初始化（在 create_app 内调用；重复调用幂等）。"""
    try:
        FastAPICache.get_backend()
        return
    except AssertionError:
        pass
    prefix = f"{settings.APP_CODE}:"
    if settings.CACHE_BACKEND == "redis":  # pragma: no cover - 依赖外部 Valkey
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=False)
        FastAPICache.init(RedisBackend(client), prefix=prefix)
    else:
        FastAPICache.init(GuardedInMemoryBackend(), prefix=prefix)


def _backend():
    try:
        return FastAPICache.get_backend()
    except AssertionError:
        # 脚本/测试直接调用场景的兜底初始化（进程内）
        init_cache(get_settings())
        return FastAPICache.get_backend()


def ckey(*parts: str) -> str:
    """构造规范 Key：ckey('knowledge', 'chunk', chunk_id) -> angentplatform:knowledge:chunk:xxx"""
    prefix = f"{get_settings().APP_CODE}:"
    joined = ":".join(str(p).strip(":") for p in parts if str(p).strip(":"))
    return f"{prefix}{joined}"


def jitter(ttl: int, ratio: float = 0.2) -> int:
    """TTL 随机抖动（规则 E-03；非加密用途）。"""
    return ttl + random.randint(0, max(1, int(ttl * ratio)))  # noqa: S311


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _decode(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


async def cache_get(key: str) -> Any | None:
    return _decode(await _backend().get(key))


async def cache_set(key: str, value: Any, ttl: int) -> None:
    await _backend().set(key, _encode(value), expire=ttl)


async def cache_add(key: str, value: Any, ttl: int) -> bool:
    """SETNX 语义：键不存在时写入并返回 True；已存在返回 False。"""
    backend = _backend()
    if isinstance(backend, RedisBackend):  # pragma: no cover - 依赖外部 Valkey
        result = await backend.redis.set(key, _encode(value), nx=True, ex=ttl)
        return bool(result)
    return await backend.add(key, _encode(value), expire=ttl)


async def cache_delete(key: str) -> None:
    backend = _backend()
    if isinstance(backend, RedisBackend):  # pragma: no cover
        await backend.redis.delete(key)
    elif hasattr(backend, "delete"):
        await backend.delete(key)
    else:  # pragma: no cover - 理论不可达
        await backend.clear(key=key)


async def cache_ping() -> bool:
    try:
        key = ckey("ops", "ping")
        await cache_set(key, "pong", ttl=10)
        return await cache_get(key) == "pong"
    except Exception:
        return False
