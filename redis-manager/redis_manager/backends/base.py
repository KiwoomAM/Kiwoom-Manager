# -*- coding: utf-8 -*-
"""redis_manager/backends/base.py

KeyValueBackend Protocol — Redis 백엔드와 메모리 백엔드의 공통 계약.

신규 명령 추가 시 이 Protocol → HandlerBackend → MemoryBackend 순으로 갱신할 것.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌(shadowing)을 회피하기 위해 필요. 모든 어노테이션이
지연 평가(lazy string)되어 클래스 정의 시점에 평가되지 않음.
"""

from __future__ import annotations

from typing import Iterator, Optional, Protocol, runtime_checkable


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

    def compare_and_delete(self, key: str, expected: str) -> bool:
        """저장된 값이 ``expected``와 정확히 일치할 때만 삭제 (atomic).

        분산 락 release의 정통 패턴 — 소유자 토큰이 맞을 때만 푼다.
        Returns: 삭제 시 True, 불일치/부재 시 False.
        """
        ...

    def compare_and_set(self, key: str, expected: str, value: str, *, ex: int) -> bool:
        """저장된 값이 ``expected``와 일치할 때만 값 교체 + TTL(``ex``) 갱신 (atomic).

        분산 락 renew의 정통 패턴. ``ex``는 필수 (TTL 없는 renew = 영구 락 footgun).
        Returns: 교체 시 True, 불일치/부재 시 False.
        """
        ...

    # ── Key ───────────────────────────────────────────
    def exists(self, key: str) -> bool: ...
    def ttl(self, key: str) -> int: ...
    def expire(self, key: str, seconds: int) -> bool: ...

    def scan_iter(self, match: str, count: int = 100) -> Iterator[str]:
        """Cursor 기반 비차단 키 SCAN.

        운영 정책: `KEYS pattern`은 단일 스레드 블로킹이라 금지 — 대용량 DB에서
        prefix 매칭이 필요하면 반드시 이 메서드 사용.

        Args:
            match: glob 패턴 (예: ``"file_cache:files_api:*"``)
            count: 매 SCAN 호출의 batch hint. 정확히 N개 yield를 보장하지 않음.

        Yields:
            패턴에 매칭되는 키 (str).

        Notes:
            - 같은 키가 여러 번 yield될 수 있음 (Redis SCAN cursor 특성). 호출자가
              dedup 책임.
            - iteration 도중 추가된 키는 누락 가능, 삭제된 키는 stale로 반환 가능.
            - 재시도 정책 미적용 — generator는 첫 batch만 retry 대상이라 의미가
              깨짐. 호출자가 외부 루프에서 재시작 책임.
        """
        ...

    # ── Set ───────────────────────────────────────────
    def sadd(self, key: str, *members: str) -> int: ...
    def smembers(self, key: str) -> set[str]: ...
    def srem(self, key: str, *members: str) -> int: ...

    # ── Health ────────────────────────────────────────
    def ping(self) -> bool: ...


__all__ = ["KeyValueBackend"]
