# -*- coding: utf-8 -*-
"""redis_handler/backends/base.py

KeyValueBackend Protocol — Redis 백엔드와 메모리 백엔드의 공통 계약.

신규 명령 추가 시 이 Protocol → RedisBackend → MemoryBackend 순으로 갱신할 것.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌(shadowing)을 회피하기 위해 필요. 모든 어노테이션이
지연 평가(lazy string)되어 클래스 정의 시점에 평가되지 않음.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class KeyValueBackend(Protocol):
    """Redis와 메모리 백엔드 모두가 구현해야 하는 키-값 저장소 계약."""

    @property
    def name(self) -> str:
        """백엔드 식별자 — 'redis' 또는 'memory'."""
        ...

    # ── String ────────────────────────────────────────
    def get(self, key: str) -> Optional[str]: ...

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
        """SET. 성공 시 True, NX/XX 조건 미충족 시 False."""
        ...

    def delete(self, key: str) -> int: ...

    # ── Key ───────────────────────────────────────────
    def exists(self, key: str) -> bool: ...
    def ttl(self, key: str) -> int: ...
    def expire(self, key: str, seconds: int) -> bool: ...

    # ── Set ───────────────────────────────────────────
    def sadd(self, key: str, *members: str) -> int: ...
    def smembers(self, key: str) -> set[str]: ...
    def srem(self, key: str, *members: str) -> int: ...

    # ── Health ────────────────────────────────────────
    def ping(self) -> bool: ...


__all__ = ["KeyValueBackend"]
