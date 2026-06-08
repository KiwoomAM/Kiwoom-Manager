# -*- coding: utf-8 -*-
"""
redis_manager/base_store.py

Redis JSON 저장소 베이스 클래스

Redis 위에 JSON 직렬화/역직렬화, 키 프리픽스, TTL 관리 등
공통 패턴을 제공하는 베이스 클래스입니다.

각 도메인 Store는 이 클래스를 상속하여 도메인 로직만 구현합니다.

사용법:
    from redis_manager import BaseJsonStore

    class MyStore(BaseJsonStore):
        def __init__(self):
            super().__init__(prefix="my:store", default_ttl=3600)

        def save(self, item_id: str, data: dict) -> bool:
            return self._set_json(self._make_key(item_id), data)

        def load(self, item_id: str) -> dict | None:
            return self._get_json(self._make_key(item_id))
"""

import json
import logging
from typing import Any, Iterator, Optional

from .handler import RedisManager

logger = logging.getLogger(__name__)


class BaseJsonStore:
    """
    Redis JSON 저장소 베이스 클래스

    공통 기능:
        - 키 프리픽스 자동 생성 (_make_key)
        - JSON 직렬화/역직렬화 (_set_json / _get_json)
        - TTL 기본값 관리
        - 에러 핸들링 (JSONDecodeError 캐치 + 로깅)

    상속 예시:
        class ChatStore(BaseJsonStore):
            def __init__(self):
                super().__init__(prefix="chat:history", default_ttl=604800)
    """

    def __init__(
        self,
        prefix: str,
        default_ttl: Optional[int] = None,
        handler: Optional[RedisManager] = None,
    ):
        """
        Args:
            prefix: Redis 키 프리픽스 (예: "chat:history", "preview:batch")
            default_ttl: 기본 TTL (초), None이면 영구 저장
            handler: RedisManager 인스턴스 (None이면 싱글톤 사용)
        """
        self._handler = handler or RedisManager()
        self._prefix = prefix
        self._default_ttl = default_ttl

    # ── 키 생성 ───────────────────────────────────────────────

    def _make_key(self, *parts: str) -> str:
        """
        프리픽스 기반 Redis 키 생성

        Args:
            *parts: 키 구성 요소들

        Returns:
            "prefix:part1:part2" 형식의 키 문자열

        Examples:
            # prefix="chat:history"
            _make_key("session-1")       → "chat:history:session-1"
            _make_key("user", "session") → "chat:history:user:session"
        """
        return ":".join([self._prefix, *parts])

    # ── JSON CRUD ─────────────────────────────────────────────

    def _get_json(self, key: str) -> Optional[Any]:
        """
        Redis에서 값을 조회하고 JSON 역직렬화

        Args:
            key: Redis 키

        Returns:
            역직렬화된 Python 객체, 없거나 파싱 실패 시 None
        """
        data = self._handler.get(key)
        if data is None:
            return None

        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"JSON 역직렬화 실패 ({key}): {e}")
            return None

    def _set_json(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        *,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        """
        Python 객체를 JSON 직렬화하여 Redis에 저장

        Args:
            key: Redis 키
            value: 저장할 Python 객체 (json.dumps 가능한 타입)
            ttl: TTL (초), None이면 default_ttl 사용
            nx: 키 없을 때만 set
            xx: 키 있을 때만 set
            keepttl: 기존 TTL 유지

        Returns:
            저장 성공 여부 (NX/XX 조건 미충족 시 False)
        """
        effective_ttl = None if keepttl else (ttl if ttl is not None else self._default_ttl)
        return self._handler.set(
            key,
            json.dumps(value, ensure_ascii=False),
            ex=effective_ttl,
            nx=nx,
            xx=xx,
            keepttl=keepttl,
        )

    def _setnx_json(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        SETNX의 JSON 버전 — 분산 잠금/standby 패턴용 편의 메서드

        Args:
            key: Redis 키
            value: 저장할 Python 객체
            ttl: TTL (초), None이면 default_ttl 사용

        Returns:
            True: 자기가 점유 (작업 시작 가능)
            False: 이미 점유됨

        Raises:
            RedisUnavailableError: Redis 미연결
            RedisRetryExhausted: retry 소진
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        return self._handler.setnx(
            key,
            json.dumps(value, ensure_ascii=False),
            ttl=effective_ttl,
        )

    # ── 키 조작 ───────────────────────────────────────────────

    def _delete(self, key: str) -> int:
        """
        키 삭제

        Args:
            key: Redis 키

        Returns:
            삭제된 키 수 (0 또는 1)
        """
        return self._handler.delete(key)

    def _exists(self, key: str) -> bool:
        """
        키 존재 여부 확인

        Args:
            key: Redis 키

        Returns:
            존재 여부
        """
        return self._handler.exists(key)

    def _ttl(self, key: str) -> int:
        """
        키의 남은 TTL 조회

        Args:
            key: Redis 키

        Returns:
            남은 초수, -1 = TTL 없음(영구), -2 = 키 없음
        """
        return self._handler.ttl(key)

    def _expire(self, key: str, seconds: int) -> bool:
        """
        키에 TTL 설정

        Args:
            key: Redis 키
            seconds: TTL (초)

        Returns:
            성공 여부
        """
        return self._handler.expire(key, seconds)

    def _scan_iter(self, pattern: str, count: int = 100) -> Iterator[str]:
        """
        프리픽스 패턴 매칭 키를 cursor 기반으로 yield (비차단).

        ``KEYS pattern``은 단일 스레드 블로킹이라 금지 — 대용량 prefix 탐색은
        반드시 이 메서드를 사용해야 한다.

        Args:
            pattern: glob 패턴. 호출 측에서 ``self._make_key(..., "*")`` 형태로
                완전한 패턴을 직접 구성한다.
            count: 매 SCAN 호출의 batch hint (정확히 N개 yield 보장 X).

        Yields:
            매칭되는 키 (str). 같은 키가 중복 yield될 수 있고, iteration 중
            mutation은 stale read 가능 (SCAN의 일반적 특성).

        Note:
            Redis 가용 중 부분 yield 후 transient 실패 시 Handler가 메모리
            백엔드로 폴백하여 나머지를 yield한다. 결과 집합이 두 백엔드의
            키를 혼합할 수 있으므로, "전체 키 공간 스냅샷"을 가정하면 안 된다.

        Examples:
            for key in self._scan_iter(self._make_key("files_api", "*")):
                ...
        """
        return self._handler.scan_iter(match=pattern, count=count)

    # ── Set 연산 위임 ─────────────────────────────────────────

    def _sadd(self, key: str, *members: str) -> int:
        """Set에 멤버 추가"""
        return self._handler.sadd(key, *members)

    def _smembers(self, key: str) -> set:
        """Set의 모든 멤버 조회"""
        return self._handler.smembers(key)

    def _srem(self, key: str, *members: str) -> int:
        """Set에서 멤버 제거"""
        return self._handler.srem(key, *members)

    # ── 유틸리티 ──────────────────────────────────────────────

    @property
    def storage_mode(self) -> str:
        """현재 저장소 모드 ("redis" 또는 "memory")"""
        return self._handler.storage_mode

    def cleanup_memory(self) -> int:
        """메모리 백엔드의 만료 키 정리"""
        return self._handler.cleanup_memory()


__all__ = [
    "BaseJsonStore",
]
