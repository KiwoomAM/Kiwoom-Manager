# -*- coding: utf-8 -*-
"""redis_manager/handler.py

RedisManager — Redis 백엔드와 메모리 백엔드를 조율하는 orchestrator.

책임:
    - 백엔드 선택 (Redis 가용성에 따라)
    - 입력 검증 (ttl/ex 충돌, nx/xx 충돌, keepttl 충돌)
    - 일반 명령 폴백 (Redis 실패 → 메모리)
    - NX/XX 폴백 차단 (분산 의미론 보호)
    - Singleton 인스턴스 관리

신규 명령 추가 시 KeyValueBackend Protocol → RedisBackend → MemoryBackend → 이 클래스 순으로 갱신.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`Set[str]` 타입 힌트의 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

import logging
from typing import Iterator, Optional, Set

from .backends import MemoryBackend, RedisBackend
from .config import RedisConfig
from .connection import close_redis, get_client, is_available
from .exceptions import RedisHandlerError, RedisUnavailableError
from .retry import RetryPolicy

logger = logging.getLogger(__name__)


class RedisManager:
    """
    Redis 기본 CRUD 핸들러 (Singleton).

    Redis 가용 시 RedisBackend(redis-py + retry)를 사용,
    실패 시 MemoryBackend로 자동 폴백한다.
    NX/XX 명령은 분산 의미론 보호를 위해 폴백을 차단하고
    RedisUnavailableError 또는 RedisRetryExhausted를 raise한다.
    """

    _instance = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._memory = MemoryBackend()
        self._retry_policy = RetryPolicy()
        self._initialized = True

    # ── 백엔드 선택 ──────────────────────────────────
    def _redis(self) -> Optional[RedisBackend]:
        """Redis 가용 시 RedisBackend, 아니면 None.

        is_available()을 먼저 확인해 get_client()의 자동 재초기화를 방지한다.
        테스트에서 connection._redis_available = False 로 직접 설정한 경우를 존중함.
        """
        if not is_available():
            # _redis_available이 False면 auto-reinit 없이 바로 None 반환
            return None
        client = get_client()
        if client is None:
            return None
        return RedisBackend(client, self._retry_policy)

    @property
    def is_redis_connected(self) -> bool:
        return self._redis() is not None

    @property
    def storage_mode(self) -> str:
        return "redis" if self.is_redis_connected else "memory"

    # ── String CRUD ──────────────────────────────────
    def get(self, key: str) -> Optional[str]:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.get(key)
            except RedisHandlerError as e:
                logger.warning("Redis GET 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.get(key)

    def set(
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
        """SET 명령. ttl(legacy)과 ex(redis-py 표준) 모두 수용.

        Raises:
            ValueError: 옵션 조합 충돌
            RedisUnavailableError: nx/xx 호출 시 Redis 미연결
            RedisRetryExhausted: nx/xx 호출 시 retry 소진
        """
        # 입력 검증
        if ttl is not None and ex is not None:
            raise ValueError("ttl과 ex는 동시 지정 불가 — 하나만 사용하세요")
        if nx and xx:
            raise ValueError("nx와 xx는 동시 사용 불가")
        effective_ttl = ttl if ttl is not None else ex
        if keepttl and effective_ttl is not None:
            raise ValueError("keepttl과 ttl/ex는 동시 사용 불가")

        # NX/XX: 폴백 차단
        if nx or xx:
            rb = self._redis()
            if rb is None:
                raise RedisUnavailableError(
                    "NX/XX 명령은 Redis 연결이 필요합니다 (메모리 폴백 시 분산 의미론 깨짐)"
                )
            return rb.set(key, value, ex=effective_ttl, nx=nx, xx=xx, keepttl=keepttl)

        # 일반 set: 폴백 허용
        rb = self._redis()
        if rb is not None:
            try:
                return rb.set(key, value, ex=effective_ttl, keepttl=keepttl)
            except RedisHandlerError as e:
                logger.warning("Redis SET 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.set(key, value, ex=effective_ttl, keepttl=keepttl)

    def setex(self, key: str, ttl: int, value: str) -> bool:
        """TTL과 함께 set. Redis SETEX 호환 시그니처."""
        return self.set(key, value, ex=ttl)

    def setnx(self, key: str, value: str, *, ttl: Optional[int] = None) -> bool:
        """키 없을 때만 set. 분산 잠금/캐시 스탬피드 방지용.

        Returns:
            True: 자기가 점유 (작업 시작 가능)
            False: 이미 점유됨 (대기/포기 분기)

        Raises:
            RedisUnavailableError: Redis 미연결
            RedisRetryExhausted: retry 소진
        """
        return self.set(key, value, nx=True, ex=ttl)

    def delete(self, key: str) -> int:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.delete(key)
            except RedisHandlerError as e:
                logger.warning("Redis DELETE 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.delete(key)

    # ── Key 연산 ─────────────────────────────────────
    def exists(self, key: str) -> bool:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.exists(key)
            except RedisHandlerError as e:
                logger.warning("Redis EXISTS 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.exists(key)

    def ttl(self, key: str) -> int:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.ttl(key)
            except RedisHandlerError as e:
                logger.warning("Redis TTL 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.ttl(key)

    def expire(self, key: str, seconds: int) -> bool:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.expire(key, seconds)
            except RedisHandlerError as e:
                logger.warning("Redis EXPIRE 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.expire(key, seconds)

    def scan_iter(self, match: str, count: int = 100) -> Iterator[str]:
        """Cursor 기반 키 SCAN.

        Redis 가용 시 RedisBackend.scan_iter를 사용, 미가용 또는 일시 장애 시
        MemoryBackend.scan_iter로 폴백한다. 다른 명령과 동일한 폴백 정책.

        주의: Redis와 메모리 백엔드는 독립된 키 공간이라 폴백 결과는 해당
        백엔드에 살아있는 키만 반영한다 — "전체 키 목록"이 아니다.

        ``KEYS``는 단일 스레드 블로킹이라 금지. ``count``는 hint일 뿐.
        같은 키가 중복 yield될 수 있고, iteration 중 mutation은 stale read 가능.

        부분 yield 후 폴백: Redis가 일부 키를 yield한 뒤 transient 실패가 나면
        나머지는 메모리 백엔드에서 yield된다 — 호출자가 받는 결과는 두 백엔드의
        키가 혼재한 형태가 될 수 있다 (예외는 raise되지 않음).
        """
        rb = self._redis()
        if rb is not None:
            try:
                yield from rb.scan_iter(match, count)
                return
            except RedisHandlerError as e:
                logger.warning("Redis SCAN 폴백 → 메모리 (%s): %s", match, e)
        yield from self._memory.scan_iter(match, count)

    # ── Set 연산 ─────────────────────────────────────
    def sadd(self, key: str, *members: str) -> int:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.sadd(key, *members)
            except RedisHandlerError as e:
                logger.warning("Redis SADD 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.sadd(key, *members)

    def smembers(self, key: str) -> Set[str]:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.smembers(key)
            except RedisHandlerError as e:
                logger.warning("Redis SMEMBERS 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.smembers(key)

    def srem(self, key: str, *members: str) -> int:
        rb = self._redis()
        if rb is not None:
            try:
                return rb.srem(key, *members)
            except RedisHandlerError as e:
                logger.warning("Redis SREM 폴백 → 메모리 (%s): %s", key, e)
        return self._memory.srem(key, *members)

    # ── 유틸리티 ─────────────────────────────────────
    def ping(self) -> bool:
        """Redis 연결 확인. Redis 미연결 시 False (메모리 백엔드는 ping 의미 없음)."""
        rb = self._redis()
        if rb is None:
            return False
        try:
            return rb.ping()
        except RedisHandlerError:
            return False

    def health_check(self) -> dict:
        return {
            "connected": self.is_redis_connected,
            "storage": self.storage_mode,
            "config": RedisConfig.get_summary(),
        }

    def close(self) -> None:
        close_redis()

    def reset(self) -> None:
        """핸들러 리셋 (테스트용): 연결 종료 + 메모리 초기화."""
        close_redis()
        self._memory.flush()

    def cleanup_memory(self) -> int:
        return self._memory.cleanup_expired()


__all__ = ["RedisManager"]
