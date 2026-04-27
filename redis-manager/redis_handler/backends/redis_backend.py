# -*- coding: utf-8 -*-
"""redis_handler/backends/redis_backend.py

redis-py 클라이언트 래핑 + 재시도 적용.

각 메서드에 @retryable 데코레이터를 적용해 일시적 장애를 자동 복구한다.
재시도 소진 시 RedisRetryExhausted를 raise (handler가 catch하여 메모리 폴백).

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

from typing import Optional

import redis

from ..retry import RetryPolicy, retryable


class RedisBackend:
    """KeyValueBackend Protocol의 redis-py 구현."""

    name = "redis"

    def __init__(self, client: redis.Redis, retry: RetryPolicy):
        self._client = client
        self._retry = retry

    # ── String ────────────────────────────────────────
    @retryable
    def get(self, key: str) -> Optional[str]:
        return self._client.get(key)

    @retryable
    def set(
        self,
        key: str,
        value: str,
        *,
        ex: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        result = self._client.set(
            key, value,
            ex=ex if ex and ex > 0 else None,
            nx=nx,
            xx=xx,
            keepttl=keepttl,
        )
        return bool(result)

    @retryable
    def delete(self, key: str) -> int:
        return self._client.delete(key)

    # ── Key ───────────────────────────────────────────
    @retryable
    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))

    @retryable
    def ttl(self, key: str) -> int:
        return self._client.ttl(key)

    @retryable
    def expire(self, key: str, seconds: int) -> bool:
        return bool(self._client.expire(key, seconds))

    # ── Set ───────────────────────────────────────────
    @retryable
    def sadd(self, key: str, *members: str) -> int:
        return self._client.sadd(key, *members)

    @retryable
    def smembers(self, key: str) -> set[str]:
        return set(self._client.smembers(key))

    @retryable
    def srem(self, key: str, *members: str) -> int:
        return self._client.srem(key, *members)

    # ── Health ────────────────────────────────────────
    @retryable
    def ping(self) -> bool:
        return bool(self._client.ping())


__all__ = ["RedisBackend"]
