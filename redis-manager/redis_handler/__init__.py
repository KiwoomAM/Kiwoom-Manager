# -*- coding: utf-8 -*-
"""
redis_handler

범용 Redis 핸들러 패키지

Redis 연결 관리 및 기본 CRUD를 메모리 폴백 포함하여 제공합니다.
비즈니스 로직은 포함하지 않으며, 어떤 서비스에서든 인프라 도구로 사용할 수 있습니다.

사용법:
    # 범용 Redis CRUD
    from redis_handler import RedisHandler
    rh = RedisHandler()
    rh.set("key", "value", ttl=3600)
    val = rh.get("key")
    rh.delete("key")

    # Set 연산
    rh.sadd("myset", "a", "b")
    members = rh.smembers("myset")

    # 설정 변경
    from redis_handler import RedisConfig
    RedisConfig.configure(url="redis://my-host:6379/0")

    # 연결 관리
    from redis_handler import init_redis, close_redis, is_available
"""

# ── Config ────────────────────────────────────────────────
from .config import RedisConfig

# ── Connection ────────────────────────────────────────────
from .connection import (
    init_redis,
    get_client,
    is_available,
    close_redis,
    reset_redis,
    check_redis_connection,
)

# ── Handler (기본 CRUD) ──────────────────────────────────
from .handler import RedisHandler

# ── BaseJsonStore (JSON 저장소 베이스) ────────────────────
from .base_store import BaseJsonStore


# ── 전역 인스턴스 ─────────────────────────────────────────
# 모듈 로드 시 Redis 초기화 시도
init_redis()

# 전역 Handler 인스턴스 (Singleton)
redis_handler = RedisHandler()


__all__ = [
    # Config
    "RedisConfig",
    # Connection (저수준)
    "init_redis",
    "get_client",
    "is_available",
    "close_redis",
    "reset_redis",
    "check_redis_connection",
    # Handler (기본 CRUD)
    "RedisHandler",
    "redis_handler",
    # BaseJsonStore (JSON 저장소 베이스)
    "BaseJsonStore",
]
