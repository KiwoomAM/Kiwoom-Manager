# -*- coding: utf-8 -*-
"""
redis_manager

범용 Redis 핸들러 패키지 — 메모리 폴백 + 재시도 + SET 옵션 내장.

비즈니스 로직은 포함하지 않으며, 어떤 서비스에서든 인프라 도구로 사용할 수 있습니다.

사용법:
    # 범용 Redis CRUD
    from redis_manager import RedisManager
    rh = RedisManager()
    rh.set("key", "value", ttl=3600)
    val = rh.get("key")

    # 분산 잠금 / 캐시 스탬피드 방지
    if rh.setnx("lock:job", "owner-A", ttl=30):
        try:
            do_work()
        finally:
            rh.delete("lock:job")

    # 토큰 갱신 (만료시각 유지)
    rh.set("session:abc", new_token, keepttl=True)
"""

# ── Config ────────────────────────────────────────────────
from .config import RedisConfig, RetryConfig

# ── Connection ────────────────────────────────────────────
from .connection import (
    init_redis,
    get_client,
    is_available,
    close_redis,
    reset_redis,
    check_redis_connection,
)

# ── Exceptions ────────────────────────────────────────────
from .exceptions import (
    RedisHandlerError,
    RedisRetryExhausted,
    RedisUnavailableError,
)

# ── Retry ─────────────────────────────────────────────────
from .retry import RetryPolicy

# ── Backends ──────────────────────────────────────────────
from .backends import KeyValueBackend, MemoryBackend, RedisBackend

# ── Handler ───────────────────────────────────────────────
from .handler import RedisManager

# ── BaseJsonStore ─────────────────────────────────────────
from .base_store import BaseJsonStore


# ── 전역 인스턴스 ─────────────────────────────────────────
# 연결은 첫 사용 시점에 get_client()가 lazy로 수행한다. import 시점에 ping하지 않는다
# (import가 블로킹되거나 configure() 전에 연결이 고정되는 것을 방지).
redis_manager = RedisManager()


__all__ = [
    # Config
    "RedisConfig",
    "RetryConfig",
    # Connection (저수준 — 신규 코드는 RedisManager 사용 권장)
    "init_redis",
    "get_client",
    "is_available",
    "close_redis",
    "reset_redis",
    "check_redis_connection",
    # Exceptions
    "RedisHandlerError",
    "RedisRetryExhausted",
    "RedisUnavailableError",
    # Retry
    "RetryPolicy",
    # Backends (Protocol + 구현체)
    "KeyValueBackend",
    "MemoryBackend",
    "RedisBackend",
    # Handler
    "RedisManager",
    "redis_manager",
    # BaseJsonStore
    "BaseJsonStore",
]
