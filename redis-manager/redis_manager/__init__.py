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

v3.0.0: transport가 redis-py 직결 → redis-handler HTTP 경유로 변경.
RedisManager/BaseJsonStore API는 v2와 100% 호환.
제거됨: RedisBackend, RetryPolicy (CLAUDE.md상 직접 사용 금지 대상이었음)
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

# ── Backends ──────────────────────────────────────────────
from .backends import HandlerBackend, KeyValueBackend, MemoryBackend

# ── Handler ───────────────────────────────────────────────
from .handler import RedisManager

# ── BaseJsonStore ─────────────────────────────────────────
from .base_store import BaseJsonStore

# ── Async 인터페이스 ──────────────────────────────────────
from redis_manager import aio


# ── 전역 인스턴스 ─────────────────────────────────────────
# HTTP 클라이언트는 첫 명령 시점에 lazy 생성된다. import 시점에 연결하지 않으며,
# import 후 RedisConfig.configure(handler_url=...) 호출도 첫 사용 전이면 반영된다.
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
    # Backends (Protocol + 구현체)
    "HandlerBackend",
    "KeyValueBackend",
    "MemoryBackend",
    # Handler
    "RedisManager",
    "redis_manager",
    # BaseJsonStore
    "BaseJsonStore",
    # Async
    "aio",
]
