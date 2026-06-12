"""cache stampede dedup — Redis standby 레코드 기반.

3계층 공통 standby 패턴:
- files_api:     file_cache:files_api_standby:{checksum}
- context_cache: file_cache:context_cache_standby:{hash_key}
- agent_llm:     agent_llm_standby:{cache_key}

redis-handler v3 의 RedisManager API 사용 — 선점(setnx)/해제(delete)는 NX 폴백 차단으로
분산 의미론 보호, standby 폴링(get)은 일반 명령 폴백 정책을 따른다(v3 설계상 read 는
항상 폴백 허용, strict-read 미지원). 대기 중 단절 시 get 이 None 폴백 → 호출자가 자기
작업 수행(timeout 폴백과 동일 경로). 상세 설계: docs/08.dedup-design-and-impl.md
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis_manager import (
    RedisManager,
    RedisRetryExhausted,
    RedisUnavailableError,
)

logger = logging.getLogger(__name__)


# ── backoff 파라미터 (§4) ────────────────────────────────────

_BACKOFF_INITIAL = 0.5
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_CAP = 10.0


# ── 키 빌더 ──────────────────────────────────────────────────


def _make_standby_key(prefix: str, key: str) -> str:
    """prefix별 full Redis 키 조립.

    file_cache 네임스페이스: files_api / context_cache (기존 본 캐시와 prefix 공유)
    별도 네임스페이스: agent_llm
    """
    if prefix in ("files_api", "context_cache"):
        return f"file_cache:{prefix}_standby:{key}"
    elif prefix == "agent_llm":
        return f"agent_llm_standby:{key}"
    else:
        raise ValueError(f"Unknown standby prefix: {prefix}")


# ── standby API ──────────────────────────────────────────────


async def try_acquire_standby(
    prefix: str,
    key: str,
    ttl_seconds: int,
) -> bool:
    """Redis SETNX 로 standby 선점 시도.

    Returns:
        True  — 자기가 선점 (작업 시작)
        False — 다른 요청이 이미 작업 중 (호출자는 wait_for_standby_release)

    Raises:
        RuntimeError: Redis 미연결 또는 retry 소진 (RedisManager 가
            RedisUnavailableError / RedisRetryExhausted raise).
    """
    standby_key = _make_standby_key(prefix, key)
    value = json.dumps({"started_at": datetime.now(UTC).isoformat()})
    try:
        return await asyncio.to_thread(
            RedisManager().setnx, standby_key, value, ttl=ttl_seconds,
        )
    except (RedisUnavailableError, RedisRetryExhausted) as e:
        raise RuntimeError("Redis 미연결 — standby 불가") from e


async def wait_for_standby_release(
    prefix: str,
    key: str,
    timeout: int,
    find_cache_fn: Callable[[], Any] | None = None,
) -> dict | None:
    """standby 사라질 때까지 exponential backoff 대기.

    standby 폴링은 RedisManager().get 사용 — v3 는 strict-read 를 지원하지 않으므로
    (read 는 항상 메모리 폴백) 대기 중 단절 시 get 이 None 을 반환하고, 호출자는
    timeout 폴백과 동일하게 자기 작업을 수행한다(graceful degradation).

    대기 중 find_cache_fn() 주기적 호출 (sync/async 모두 허용):
      - HIT → 캐시 dict 반환 (즉시 종료)
      - MISS + standby 사라짐 → None 반환 (호출자가 작업 시작)
      - timeout → None 반환 + 경고 로그 (Y' Hybrid)

    backoff 시퀀스: 0.5 → 1 → 2 → 4 → 8 → 10 (cap) → 10 → ...

    Args:
        prefix: "files_api" / "context_cache" / "agent_llm"
        key: 해시 값 (checksum / hash_key / cache_key)
        timeout: 전체 대기 timeout (= standby_ttl_seconds)
        find_cache_fn: 본 캐시 조회 함수 (sync 또는 async callable)

    Returns:
        HIT → 캐시 dict, MISS/timeout → None
    """
    standby_key = _make_standby_key(prefix, key)
    delay = _BACKOFF_INITIAL
    elapsed = 0.0

    while elapsed < timeout:
        await asyncio.sleep(delay)
        elapsed += delay

        # 본 캐시 재조회 (HIT면 종료)
        if find_cache_fn is not None:
            result = find_cache_fn()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return result

        # standby 폴링 — 단절 시 None 폴백(strict-read 미지원), 호출자가 자기 작업 수행
        if await asyncio.to_thread(RedisManager().get, standby_key) is None:
            return None

        # 다음 대기 간격
        delay = min(delay * _BACKOFF_MULTIPLIER, _BACKOFF_CAP)

    # timeout — Y' Hybrid (호출자가 작업 시작)
    logger.warning(
        "standby 대기 timeout(%d초) 초과 — 호출자가 작업 시작. "
        "standby_key=%s, 중복 발생 가능. 빈도 높으면 standby_ttl_seconds 늘리세요.",
        timeout, standby_key,
    )
    return None


async def release_standby(prefix: str, key: str) -> None:
    """standby DEL. finally 블록용.

    Redis 미연결/장애 시 예외는 삼킴 — 호출자 흐름 방해 방지.
    남은 standby는 TTL로 자연 정리됨.
    """
    try:
        standby_key = _make_standby_key(prefix, key)
        await asyncio.to_thread(RedisManager().delete, standby_key)
    except Exception:
        logger.exception(
            "standby release 실패 (무시, TTL로 자연 정리): prefix=%s, key=%s",
            prefix, key[:16] if key else "",
        )
