# -*- coding: utf-8 -*-
"""redis_manager.aio — 비동기 인터페이스.

다른 *_manager.aio와 동일한 컨벤션: 비싱글톤, 명시적 인스턴스 + aclose().

사용법:
    from redis_manager.aio import AsyncRedisManager

    async with AsyncRedisManager() as rh:
        await rh.set("key", "value", ttl=3600)
        val = await rh.get("key")
"""

from .backend import AsyncHandlerBackend
from .base_store import AsyncBaseJsonStore
from .handler import AsyncRedisManager

__all__ = [
    "AsyncHandlerBackend",
    "AsyncRedisManager",
    "AsyncBaseJsonStore",
]
