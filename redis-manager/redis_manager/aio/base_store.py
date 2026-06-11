# -*- coding: utf-8 -*-
"""redis_manager/aio/base_store.py

AsyncBaseJsonStore — Redis JSON 저장소 async 베이스 클래스.

sync BaseJsonStore의 async 미러. 전역 싱글톤이 없으므로 AsyncRedisManager 인스턴스를
생성자에 주입 필수 — lifecycle 소유권을 소비자에게 둔다.

사용법:
    from redis_manager.aio import AsyncRedisManager, AsyncBaseJsonStore

    class ChatStore(AsyncBaseJsonStore):
        def __init__(self, handler):
            super().__init__(prefix="chat:history", default_ttl=604800, handler=handler)

        async def save(self, sid, data):
            return await self._set_json(self._make_key(sid), data)
"""

import json
import logging
from typing import Any, AsyncIterator, Optional

from .handler import AsyncRedisManager

logger = logging.getLogger(__name__)


class AsyncBaseJsonStore:
    """Redis JSON 저장소 async 베이스 클래스."""

    def __init__(
        self,
        prefix: str,
        default_ttl: Optional[int] = None,
        *,
        handler: AsyncRedisManager,
    ):
        """
        Args:
            prefix: Redis 키 프리픽스 (예: "chat:history")
            default_ttl: 기본 TTL (초), None이면 영구 저장
            handler: AsyncRedisManager 인스턴스 (주입 필수)
        """
        self._handler = handler
        self._prefix = prefix
        self._default_ttl = default_ttl

    # ── 키 생성 (sync — 순수 문자열) ──────────────────
    def _make_key(self, *parts: str) -> str:
        """프리픽스 기반 Redis 키 생성 ("prefix:part1:part2")."""
        return ":".join([self._prefix, *parts])

    # ── JSON CRUD ─────────────────────────────────────
    async def _get_json(self, key: str) -> Optional[Any]:
        """Redis에서 값을 조회하고 JSON 역직렬화. 없거나 파싱 실패 시 None."""
        data = await self._handler.get(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"JSON 역직렬화 실패 ({key}): {e}")
            return None

    async def _set_json(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        *,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        """Python 객체를 JSON 직렬화하여 Redis에 저장."""
        effective_ttl = None if keepttl else (ttl if ttl is not None else self._default_ttl)
        return await self._handler.set(
            key,
            json.dumps(value, ensure_ascii=False),
            ex=effective_ttl,
            nx=nx,
            xx=xx,
            keepttl=keepttl,
        )

    async def _setnx_json(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """SETNX의 JSON 버전 — 분산 잠금/standby 패턴용.

        Raises:
            RedisUnavailableError: handler 미도달
            RedisRetryExhausted: retry 소진
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        return await self._handler.setnx(
            key,
            json.dumps(value, ensure_ascii=False),
            ttl=effective_ttl,
        )

    # ── 키 조작 ───────────────────────────────────────
    async def _delete(self, key: str) -> int:
        return await self._handler.delete(key)

    async def _exists(self, key: str) -> bool:
        return await self._handler.exists(key)

    async def _ttl(self, key: str) -> int:
        return await self._handler.ttl(key)

    async def _expire(self, key: str, seconds: int) -> bool:
        return await self._handler.expire(key, seconds)

    async def _scan_iter(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        """프리픽스 패턴 매칭 키를 cursor 기반으로 yield (비차단).

        호출 측에서 `self._make_key(..., "*")` 형태로 완전한 패턴을 구성한다.
        """
        async for key in self._handler.scan_iter(match=pattern, count=count):
            yield key

    # ── Set 연산 위임 ─────────────────────────────────
    async def _sadd(self, key: str, *members: str) -> int:
        return await self._handler.sadd(key, *members)

    async def _smembers(self, key: str) -> set:
        return await self._handler.smembers(key)

    async def _srem(self, key: str, *members: str) -> int:
        return await self._handler.srem(key, *members)

    # ── 유틸리티 ──────────────────────────────────────
    async def storage_mode(self) -> str:
        """현재 저장소 모드 ("redis" 또는 "memory") — live HTTP 1회."""
        return (await self._handler.health_check())["storage"]

    def cleanup_memory(self) -> int:
        """메모리 백엔드의 만료 키 정리."""
        return self._handler.cleanup_memory()


__all__ = ["AsyncBaseJsonStore"]
