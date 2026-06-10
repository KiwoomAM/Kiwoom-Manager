# -*- coding: utf-8 -*-
"""redis_manager/backends/memory_backend.py

In-memory 키-값 백엔드. Redis 불가 시 폴백용.

기존 _MemoryBackend(handler.py)를 이전하고 NX/XX/keepttl simulate 추가.
threading.Lock으로 동시 접근 보호.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from typing import Iterator, Optional


class MemoryBackend:
    """KeyValueBackend Protocol의 in-memory 구현."""

    name = "memory"

    def __init__(self):
        self._strings: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()

    # ── 내부 유틸 (lock 안에서 호출) ─────────────────
    def _is_expired(self, key: str) -> bool:
        if key in self._expiry and time.time() > self._expiry[key]:
            self._remove_key(key)
            return True
        return False

    def _remove_key(self, key: str) -> None:
        self._strings.pop(key, None)
        self._sets.pop(key, None)
        self._expiry.pop(key, None)

    # ── String ────────────────────────────────────────
    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if self._is_expired(key):
                return None
            return self._strings.get(key)

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
        with self._lock:
            is_expired = self._is_expired(key)
            exists = (key in self._strings or key in self._sets) and not is_expired

            if nx and exists:
                return False
            if xx and not exists:
                return False

            self._strings[key] = value

            if ex is not None and ex > 0:
                self._expiry[key] = time.time() + ex
            elif keepttl:
                pass  # 기존 expiry 유지
            else:
                self._expiry.pop(key, None)
            return True

    def delete(self, key: str) -> int:
        with self._lock:
            existed = key in self._strings or key in self._sets
            self._remove_key(key)
            return 1 if existed else 0

    # ── Key ───────────────────────────────────────────
    def exists(self, key: str) -> bool:
        with self._lock:
            if self._is_expired(key):
                return False
            return key in self._strings or key in self._sets

    def ttl(self, key: str) -> int:
        with self._lock:
            if self._is_expired(key):
                return -2
            if key not in self._strings and key not in self._sets:
                return -2
            if key not in self._expiry:
                return -1
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

    def scan_iter(self, match: str, count: int = 100) -> Iterator[str]:
        """fnmatch 기반 SCAN simulate. HandlerBackend와 인터페이스 호환.

        Lock 안에서 키 snapshot을 만든 뒤 lock 밖에서 yield — generator 소비
        도중 다른 스레드가 mutation을 기다리며 blocking되는 상황을 회피.
        ``_is_expired``가 mutation을 발생시키므로 반드시 lock 내부에서 호출.

        ``count`` 인자는 인터페이스 호환용으로 수신만 하고 무시한다 (메모리
        백엔드는 batch 의미가 없음).
        """
        del count
        with self._lock:
            candidates = list(self._strings.keys() | self._sets.keys())
            snapshot = [k for k in candidates if not self._is_expired(k)]
        for key in snapshot:
            if fnmatch.fnmatchcase(key, match):
                yield key

    # ── Set ───────────────────────────────────────────
    def sadd(self, key: str, *members: str) -> int:
        with self._lock:
            self._is_expired(key)
            if key not in self._sets:
                self._sets[key] = set()
            before = len(self._sets[key])
            self._sets[key].update(members)
            return len(self._sets[key]) - before

    def smembers(self, key: str) -> set[str]:
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

    # ── Health ────────────────────────────────────────
    def ping(self) -> bool:
        return True  # in-memory는 항상 살아있음

    # ── 메모리 전용 ───────────────────────────────────
    def cleanup_expired(self) -> int:
        """만료된 키 수동 정리. 메모리 전용."""
        now = time.time()
        with self._lock:
            expired = [k for k, exp in self._expiry.items() if now > exp]
            for k in expired:
                self._remove_key(k)
            return len(expired)

    def flush(self) -> None:
        """전체 데이터 삭제. 테스트/리셋용."""
        with self._lock:
            self._strings.clear()
            self._sets.clear()
            self._expiry.clear()


__all__ = ["MemoryBackend"]
