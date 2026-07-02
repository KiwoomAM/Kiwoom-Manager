# -*- coding: utf-8 -*-
"""redis_manager/aio/handler.py

AsyncRedisManager — async Redis CRUD 핸들러 (비싱글톤).

sync RedisManager의 async 미러. handler HTTP(AsyncHandlerBackend) 성공 시 그 결과를,
실패 시 sync MemoryBackend로 폴백한다. NX/XX는 분산 의미론 보호를 위해 폴백을 차단하고
RedisUnavailableError 또는 RedisRetryExhausted를 raise한다.

Singleton을 의도적으로 피한다 — async 환경(FastAPI lifespan, LangGraph)에서는 명시적
인스턴스 + 명시적 aclose()가 자원 관리에 안전하다 (mdb AsyncMongoDBManager와 동일).

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`Set[str]` 타입 힌트의 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional, Set

from ..backends import MemoryBackend
from ..config import RedisConfig
from ..exceptions import RedisHandlerError
from .backend import AsyncHandlerBackend

logger = logging.getLogger(__name__)


class AsyncRedisManager:
    """Redis 기본 CRUD async 핸들러 (비싱글톤)."""

    def __init__(self) -> None:
        self._memory = MemoryBackend()
        self._backend = AsyncHandlerBackend()
        self._closed = False

    # ── lifecycle ────────────────────────────────────
    async def aclose(self) -> None:
        if self._closed:
            return
        await self._backend.aclose()
        self._closed = True

    async def __aenter__(self) -> "AsyncRedisManager":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "AsyncRedisManager is closed. 새 인스턴스를 만들거나 aclose 호출 전에 사용하세요."
            )

    # ── 폴백 디스패치 ────────────────────────────────
    async def _dispatch(self, op: str, *args, **kwargs):
        """일반 명령 공용 폴백: backend 실패(RedisHandlerError) 시 sync MemoryBackend로.

        전제: args[0]는 항상 키. NX/XX·compare_*에는 쓰지 않는다. backend는 await,
        메모리 폴백은 sync 호출(별도 async 메모리 백엔드 없음).
        """
        self._ensure_open()
        try:
            return await getattr(self._backend, op)(*args, **kwargs)
        except RedisHandlerError as e:
            logger.warning("Redis %s 폴백 → 메모리 (%s): %s", op.upper(), args[0], e)
        return getattr(self._memory, op)(*args, **kwargs)

    # ── String CRUD ──────────────────────────────────
    async def get(self, key: str) -> Optional[str]:
        return await self._dispatch("get", key)

    async def set(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None,
        *,
        ex: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        """SET. ttl(legacy)과 ex(redis-py 표준) 모두 수용.

        Raises:
            ValueError: 옵션 조합 충돌
            RedisUnavailableError: nx/xx 호출 시 handler 미도달
            RedisRetryExhausted: nx/xx 호출 시 retry 소진
        """
        self._ensure_open()
        if ttl is not None and ex is not None:
            raise ValueError("ttl과 ex는 동시 지정 불가 — 하나만 사용하세요")
        if nx and xx:
            raise ValueError("nx와 xx는 동시 사용 불가")
        effective_ttl = ttl if ttl is not None else ex
        if keepttl and effective_ttl is not None:
            raise ValueError("keepttl과 ttl/ex는 동시 사용 불가")

        # NX/XX: 폴백 차단 — handler 미도달 시 RedisUnavailableError,
        # 503(retry 소진) 시 RedisRetryExhausted가 전파된다.
        if nx or xx:
            return await self._backend.set(
                key, value, ex=effective_ttl, nx=nx, xx=xx, keepttl=keepttl
            )

        # 일반 set: 폴백 허용
        return await self._dispatch("set", key, value, ex=effective_ttl, keepttl=keepttl)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        """TTL과 함께 set. Redis SETEX 호환 시그니처."""
        return await self.set(key, value, ex=ttl)

    async def setnx(self, key: str, value: str, *, ttl: Optional[int] = None) -> bool:
        """키 없을 때만 set. 분산 잠금/캐시 스탬피드 방지용.

        Raises:
            RedisUnavailableError: handler 미도달
            RedisRetryExhausted: retry 소진
        """
        return await self.set(key, value, nx=True, ex=ttl)

    async def delete(self, key: str) -> int:
        return await self._dispatch("delete", key)

    async def compare_and_delete(self, key: str, expected: str) -> bool:
        """소유자 토큰 일치 시에만 atomic 삭제 (분산 락 release).

        NX/XX와 동일하게 폴백 차단 — handler 미도달 시 RedisUnavailableError,
        재시도 소진 시 RedisRetryExhausted를 raise한다 (메모리 폴백 없음).

        재시도 계약(중요): "첫 EVAL이 키를 삭제한 뒤 응답 전 연결 끊김"을 handler가
        재시도하면 False를 돌려줄 수 있다. False는 "해제 실패"가 아니라 "현재 내
        소유가 아님(이미 해제 포함)"을 뜻하므로 에러로 다루지 말 것 (상태 멱등, 반환값 비멱등).
        """
        self._ensure_open()
        return await self._backend.compare_and_delete(key, expected)

    async def compare_and_set(self, key: str, expected: str, value: str, *, ex: int) -> bool:
        """소유자 토큰 일치 시에만 atomic 값 교체 + TTL 갱신 (분산 락 renew).

        ``ex``는 필수 (TTL 없는 renew = 영구 락). 폴백 차단은 compare_and_delete와 동일.
        """
        self._ensure_open()
        return await self._backend.compare_and_set(key, expected, value, ex=ex)

    # ── Key 연산 ─────────────────────────────────────
    async def exists(self, key: str) -> bool:
        return await self._dispatch("exists", key)

    async def ttl(self, key: str) -> int:
        return await self._dispatch("ttl", key)

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._dispatch("expire", key, seconds)

    async def scan_iter(self, match: str, count: int = 100) -> AsyncIterator[str]:
        """Cursor 기반 키 SCAN. handler 미가용/일시 장애 시 메모리 폴백.

        주의: Redis와 메모리 백엔드는 독립 키 공간이라 폴백 결과는 해당 백엔드에
        살아있는 키만 반영한다. 부분 yield 후 폴백 시 두 백엔드 키가 혼재할 수 있다.
        """
        self._ensure_open()
        try:
            async for key in self._backend.scan_iter(match, count):
                yield key
            return
        except RedisHandlerError as e:
            logger.warning("Redis SCAN 폴백 → 메모리 (%s): %s", match, e)
        for key in self._memory.scan_iter(match, count):
            yield key

    # ── Set 연산 ─────────────────────────────────────
    async def sadd(self, key: str, *members: str) -> int:
        return await self._dispatch("sadd", key, *members)

    async def smembers(self, key: str) -> Set[str]:
        return await self._dispatch("smembers", key)

    async def srem(self, key: str, *members: str) -> int:
        return await self._dispatch("srem", key, *members)

    # ── 진단 (live, HTTP 왕복) ────────────────────────
    async def ping(self) -> bool:
        """Redis 연결 확인 (live). handler 미도달/Redis 다운 모두 False."""
        self._ensure_open()
        return await self._backend.ping()

    async def health_check(self) -> dict:
        """진단 — 매 호출 handler의 live PING 결과를 조회한다."""
        self._ensure_open()
        try:
            upstream = await self._backend.health()
        except RedisHandlerError as e:
            upstream = {"status": "error", "error": str(e)}
        connected = upstream.get("status") == "connected"
        return {
            "connected": connected,
            "storage": "redis" if connected else "memory",
            "config": RedisConfig.get_summary(),
            "handler": upstream,
        }

    def cleanup_memory(self) -> int:
        return self._memory.cleanup_expired()


__all__ = ["AsyncRedisManager"]
