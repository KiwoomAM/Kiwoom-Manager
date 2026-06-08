"""cache stampede dedup — Redis standby 레코드 기반.

3계층 공통 standby 패턴:
- files_api:     file_cache:files_api_standby:{checksum}
- context_cache: file_cache:context_cache_standby:{hash_key}
- agent_llm:     agent_llm_standby:{cache_key}

redis-handler v2.0.0 의 RedisHandler API 사용 (setnx / delete) — NX 폴백 차단으로
분산 의미론 보호. 단 wait_for_standby_release 의 get 만 raw `get_client()` 를
의도적으로 유지 — handler.get 의 메모리 폴백 (단절 시 silent dedup bypass) 회피.
상세 설계: docs/cache-stampede-dedup.md, docs/15.redis-handler-v2-migration.md (§3.4)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis_handler import RedisHandler, RedisUnavailableError, get_client

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
        RuntimeError: Redis 미연결 (RedisHandler 가 RedisUnavailableError raise).
    """
    standby_key = _make_standby_key(prefix, key)
    value = json.dumps({"started_at": datetime.now(UTC).isoformat()})
    try:
        return await asyncio.to_thread(
            RedisHandler().setnx, standby_key, value, ttl=ttl_seconds,
        )
    except RedisUnavailableError as e:
        raise RuntimeError("Redis 미연결 — standby 불가") from e


async def wait_for_standby_release(
    prefix: str,
    key: str,
    timeout: int,
    find_cache_fn: Callable[[], Any] | None = None,
) -> dict | None:
    """standby 사라질 때까지 exponential backoff 대기.

    엄격 의미론 채택 (docs/15.redis-handler-v2-migration.md §3.4):
    raw `get_client()` 사용. Redis 단절 시 ConnectionError 전파로 silent dedup
    bypass 차단. RedisHandler.get 의 메모리 폴백 의도적 우회.
    redis-handler 가 strict 옵션 제공 시 이 raw 사용도 마이그레이션 가능.

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
    client = get_client()
    if client is None:
        raise RuntimeError("Redis 미연결 — standby 불가")

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

        # raw client.get — 단절 시 ConnectionError raise (handler 폴백 차단 의도)
        if await asyncio.to_thread(client.get, standby_key) is None:
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
        await asyncio.to_thread(RedisHandler().delete, standby_key)
    except Exception:
        logger.exception(
            "standby release 실패 (무시, TTL로 자연 정리): prefix=%s, key=%s",
            prefix, key[:16] if key else "",
        )
