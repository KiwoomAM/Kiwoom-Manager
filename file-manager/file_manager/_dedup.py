# -*- coding: utf-8 -*-
"""cross-process dedup — Redis standby(SETNX) 기반.

register_or_get 의 check→upload race 를 프로세스·워커 넘어 직렬화한다.
llm-manager _internal/file_processing/dedup.py 의 standby 패턴을 file-manager 용으로 이식.

Redis 미연결/타임아웃 시 graceful degradation — 호출자가 직접 업로드한다(드물게 중복 가능).
가용성 우선: standby 실패가 파일 등록 자체를 막지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from redis_manager import (
    RedisManager,
    RedisRetryExhausted,
    RedisUnavailableError,
)

logger = logging.getLogger(__name__)

STANDBY_TTL_SECONDS = 120  # 큰 파일 업로드+메타기록을 넉넉히 덮는 값
STANDBY_MAX_ROUNDS = 5  # 재선점 루프 상한 — 초과 시 직접 업로드로 degrade(무한루프 방지)
_BACKOFF_INITIAL = 0.5
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_CAP = 10.0


def standby_key(metadata: dict) -> str:
    """dedup 키(file_name+file_mtime+file_size) → Redis standby 키.

    파일명이 한글/공백/특수문자를 담을 수 있어 sha1로 해시(키 길이·문자 안전).
    """
    raw = f"{metadata['file_name']}|{metadata['file_mtime']}|{metadata['file_size']}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"file_cache:register_standby:{digest}"


async def try_acquire_standby(
    key: str, ttl_seconds: int = STANDBY_TTL_SECONDS
) -> bool:
    """SETNX로 standby 선점.

    Returns:
        True  — 내가 선점(업로드 수행)
        False — 이미 다른 요청이 작업 중(wait_for_standby_release로 대기)

    Raises:
        RuntimeError — Redis 미연결/retry 소진. 호출자가 degrade 처리.
    """
    value = json.dumps({"started_at": datetime.now(timezone.utc).isoformat()})
    try:
        return await asyncio.to_thread(
            RedisManager().setnx, key, value, ttl=ttl_seconds
        )
    except (RedisUnavailableError, RedisRetryExhausted) as e:
        raise RuntimeError("Redis 미연결 — standby 불가") from e


async def wait_for_standby_release(
    key: str,
    timeout: int,
    find_cache_fn: Callable[[], Any],
) -> Any | None:
    """캐시 HIT까지 exponential backoff 대기. 성공 신호는 오직 캐시 HIT, 종료는 timeout.

    매 주기 find_cache_fn()을 호출(sync/async 모두 허용):
      - HIT → 캐시 결과 반환(즉시 종료)
      - timeout → None 반환 + 경고 (호출자의 재선점 루프가 이관받음)

    standby 존재를 get()으로 조기 판정하지 않는다 — v3 read는 순간 blip에도 메모리
    폴백(None)이라 "해제됨"을 오독해 선제 중복 업로드를 유발한다(strict-read 미지원).
    winner가 정말 죽어 캐시가 안 써지면 timeout까지 기다렸다가 호출자가 재선점한다.

    backoff: 0.5 → 1 → 2 → 4 → 8 → 10(cap) → ...
    """
    delay = _BACKOFF_INITIAL
    elapsed = 0.0
    while elapsed < timeout:
        await asyncio.sleep(delay)
        elapsed += delay

        result = find_cache_fn()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result

        delay = min(delay * _BACKOFF_MULTIPLIER, _BACKOFF_CAP)

    logger.warning(
        "standby 대기 timeout(%d초) 초과 — 재선점/직접 업로드로 이관. key=%s",
        timeout, key,
    )
    return None


async def release_standby(key: str) -> None:
    """standby DEL(finally 용). 실패는 삼킴 — 남은 키는 TTL로 자연 정리."""
    try:
        await asyncio.to_thread(RedisManager().delete, key)
    except Exception:
        logger.exception("standby release 실패(무시, TTL 정리): key=%s", key)
