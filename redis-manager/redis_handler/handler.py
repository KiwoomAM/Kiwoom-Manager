# -*- coding: utf-8 -*-
"""
redis_handler/handler.py

Redis 기본 CRUD 핸들러 (Singleton)

모든 Redis 기본 명령어를 메모리 폴백 포함하여 제공합니다.
Redis 연결이 없어도 동일한 인터페이스로 동작하므로,
상위 Store나 외부 코드에서 Redis 가용 여부를 신경쓸 필요가 없습니다.

사용법:
    from redis_handler import RedisHandler

    rh = RedisHandler()

    # String CRUD
    rh.set("key", "value")
    rh.set("key2", "value2", ttl=3600)
    value = rh.get("key")
    rh.delete("key")

    # Set 연산
    rh.sadd("myset", "a", "b", "c")
    members = rh.smembers("myset")
    rh.srem("myset", "a")

    # TTL / Key
    rh.expire("key2", 600)
    remaining = rh.ttl("key2")
    rh.exists("key2")
"""

import time
import logging
import threading
from typing import Optional, Set

from .config import RedisConfig
from .connection import get_client, is_available, init_redis, close_redis

logger = logging.getLogger(__name__)


class _MemoryBackend:
    """
    Redis 메모리 폴백 백엔드

    Redis 불가 시 동일한 인터페이스를 딕셔너리 기반으로 제공합니다.
    TTL도 타임스탬프 기반으로 시뮬레이션합니다.
    Thread-safe 하게 동작합니다.
    """

    def __init__(self):
        self._strings: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self._expiry: dict[str, float] = {}  # key -> expiry unix timestamp
        self._lock = threading.Lock()

    def _is_expired(self, key: str) -> bool:
        """키가 만료되었는지 확인 (내부용, lock 안에서 호출)"""
        if key in self._expiry:
            if time.time() > self._expiry[key]:
                self._remove_key(key)
                return True
        return False

    def _remove_key(self, key: str) -> None:
        """키 완전 삭제 (내부용, lock 안에서 호출)"""
        self._strings.pop(key, None)
        self._sets.pop(key, None)
        self._expiry.pop(key, None)

    # ── String 연산 ────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if self._is_expired(key):
                return None
            return self._strings.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        with self._lock:
            self._strings[key] = value
            if ttl is not None and ttl > 0:
                self._expiry[key] = time.time() + ttl
            elif key in self._expiry:
                del self._expiry[key]
            return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        return self.set(key, value, ttl=ttl)

    def delete(self, key: str) -> int:
        with self._lock:
            existed = key in self._strings or key in self._sets
            self._remove_key(key)
            return 1 if existed else 0

    # ── Key 연산 ───────────────────────────────────────────

    def exists(self, key: str) -> int:
        with self._lock:
            if self._is_expired(key):
                return 0
            return 1 if (key in self._strings or key in self._sets) else 0

    def ttl(self, key: str) -> int:
        with self._lock:
            if self._is_expired(key):
                return -2  # 키 없음
            if key not in self._strings and key not in self._sets:
                return -2  # 키 없음
            if key not in self._expiry:
                return -1  # TTL 없음 (영구)
            remaining = int(self._expiry[key] - time.time())
            return max(remaining, 0)

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            if self._is_expired(key):
                return False
            if key not in self._strings and key not in self._sets:
                return False
            self._expiry[key] = time.time() + seconds
            return True

    # ── Set 연산 ───────────────────────────────────────────

    def sadd(self, key: str, *members: str) -> int:
        with self._lock:
            if self._is_expired(key):
                pass  # 만료된 키면 새로 생성
            if key not in self._sets:
                self._sets[key] = set()
            before = len(self._sets[key])
            self._sets[key].update(members)
            return len(self._sets[key]) - before

    def smembers(self, key: str) -> Set[str]:
        with self._lock:
            if self._is_expired(key):
                return set()
            return set(self._sets.get(key, set()))

    def srem(self, key: str, *members: str) -> int:
        with self._lock:
            if self._is_expired(key):
                return 0
            if key not in self._sets:
                return 0
            before = len(self._sets[key])
            self._sets[key] -= set(members)
            return before - len(self._sets[key])

    # ── 유틸리티 ───────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """만료된 키 수동 정리"""
        now = time.time()
        count = 0
        with self._lock:
            expired = [k for k, exp in self._expiry.items() if now > exp]
            for k in expired:
                self._remove_key(k)
                count += 1
        return count

    def flush(self) -> None:
        """전체 데이터 삭제"""
        with self._lock:
            self._strings.clear()
            self._sets.clear()
            self._expiry.clear()


class RedisHandler:
    """
    Redis 기본 CRUD 핸들러 (Singleton)

    Redis 서버가 가용하면 redis-py를 사용하고,
    불가하면 메모리 백엔드로 투명하게 폴백합니다.
    호출자는 Redis 가용 여부를 신경쓸 필요 없이 동일한 API를 사용합니다.

    사용법:
        rh = RedisHandler()
        rh.set("mykey", "myvalue", ttl=3600)
        val = rh.get("mykey")
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._memory = _MemoryBackend()
        self._initialized = True

    # ── 연결 상태 ──────────────────────────────────────────

    @property
    def is_redis_connected(self) -> bool:
        """Redis 서버 연결 여부"""
        return is_available()

    @property
    def storage_mode(self) -> str:
        """현재 저장소 모드 ("redis" 또는 "memory")"""
        return "redis" if self.is_redis_connected else "memory"

    # ── String CRUD ────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        """
        키의 값 조회

        Args:
            key: Redis 키

        Returns:
            값 문자열, 없으면 None
        """
        client = get_client()
        if client is not None:
            try:
                return client.get(key)
            except Exception as e:
                logger.warning(f"Redis GET 실패 ({key}): {e}")
        return self._memory.get(key)

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """
        키-값 저장

        Args:
            key: Redis 키
            value: 저장할 값
            ttl: TTL (초), None이면 영구 저장

        Returns:
            성공 여부
        """
        client = get_client()
        if client is not None:
            try:
                if ttl is not None and ttl > 0:
                    client.setex(key, ttl, value)
                else:
                    client.set(key, value)
                return True
            except Exception as e:
                logger.warning(f"Redis SET 실패 ({key}): {e}")
        return self._memory.set(key, value, ttl=ttl)

    def setex(self, key: str, ttl: int, value: str) -> bool:
        """
        TTL과 함께 키-값 저장

        Args:
            key: Redis 키
            ttl: TTL (초)
            value: 저장할 값

        Returns:
            성공 여부
        """
        return self.set(key, value, ttl=ttl)

    def delete(self, key: str) -> int:
        """
        키 삭제

        Args:
            key: Redis 키

        Returns:
            삭제된 키 수 (0 또는 1)
        """
        client = get_client()
        if client is not None:
            try:
                return client.delete(key)
            except Exception as e:
                logger.warning(f"Redis DELETE 실패 ({key}): {e}")
        return self._memory.delete(key)

    # ── Key 연산 ───────────────────────────────────────────

    def exists(self, key: str) -> bool:
        """
        키 존재 여부

        Args:
            key: Redis 키

        Returns:
            존재 여부
        """
        client = get_client()
        if client is not None:
            try:
                return bool(client.exists(key))
            except Exception as e:
                logger.warning(f"Redis EXISTS 실패 ({key}): {e}")
        return bool(self._memory.exists(key))

    def ttl(self, key: str) -> int:
        """
        키의 남은 TTL 조회

        Args:
            key: Redis 키

        Returns:
            남은 초수, -1 = TTL 없음(영구), -2 = 키 없음
        """
        client = get_client()
        if client is not None:
            try:
                return client.ttl(key)
            except Exception as e:
                logger.warning(f"Redis TTL 실패 ({key}): {e}")
        return self._memory.ttl(key)

    def expire(self, key: str, seconds: int) -> bool:
        """
        키에 TTL 설정

        Args:
            key: Redis 키
            seconds: TTL (초)

        Returns:
            성공 여부
        """
        client = get_client()
        if client is not None:
            try:
                return bool(client.expire(key, seconds))
            except Exception as e:
                logger.warning(f"Redis EXPIRE 실패 ({key}): {e}")
        return self._memory.expire(key, seconds)

    # ── Set 연산 ───────────────────────────────────────────

    def sadd(self, key: str, *members: str) -> int:
        """
        Set에 멤버 추가

        Args:
            key: Redis 키
            *members: 추가할 멤버들

        Returns:
            새로 추가된 멤버 수
        """
        client = get_client()
        if client is not None:
            try:
                return client.sadd(key, *members)
            except Exception as e:
                logger.warning(f"Redis SADD 실패 ({key}): {e}")
        return self._memory.sadd(key, *members)

    def smembers(self, key: str) -> Set[str]:
        """
        Set의 모든 멤버 조회

        Args:
            key: Redis 키

        Returns:
            멤버 Set
        """
        client = get_client()
        if client is not None:
            try:
                return client.smembers(key)
            except Exception as e:
                logger.warning(f"Redis SMEMBERS 실패 ({key}): {e}")
        return self._memory.smembers(key)

    def srem(self, key: str, *members: str) -> int:
        """
        Set에서 멤버 제거

        Args:
            key: Redis 키
            *members: 제거할 멤버들

        Returns:
            제거된 멤버 수
        """
        client = get_client()
        if client is not None:
            try:
                return client.srem(key, *members)
            except Exception as e:
                logger.warning(f"Redis SREM 실패 ({key}): {e}")
        return self._memory.srem(key, *members)

    # ── 유틸리티 ───────────────────────────────────────────

    def ping(self) -> bool:
        """Redis 연결 확인"""
        client = get_client()
        if client is not None:
            try:
                return client.ping()
            except Exception:
                return False
        return False

    def health_check(self) -> dict:
        """
        헬스 체크

        Returns:
            {"connected": bool, "storage": str, "config": dict}
        """
        return {
            "connected": self.is_redis_connected,
            "storage": self.storage_mode,
            "config": RedisConfig.get_summary(),
        }

    def close(self) -> None:
        """Redis 연결 종료"""
        close_redis()

    def reset(self) -> None:
        """핸들러 리셋 (테스트용): 연결 종료 + 메모리 초기화"""
        close_redis()
        self._memory.flush()

    def cleanup_memory(self) -> int:
        """메모리 백엔드의 만료 키 정리"""
        return self._memory.cleanup_expired()


__all__ = [
    "RedisHandler",
]
