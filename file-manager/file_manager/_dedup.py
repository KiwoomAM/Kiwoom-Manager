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
import logging
import uuid
from collections.abc import Callable
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
) -> str | None:
    """SETNX로 standby 선점. owner 토큰을 값에 담아 반환한다.

    release가 이 토큰으로만 삭제하므로, 만료 후 남이 재선점한 키를 뒤늦게 지우지 않는다.

    Returns:
        str  — 선점 성공(발급된 owner 토큰; 그대로 release_standby에 넘긴다)
        None — 이미 다른 요청이 작업 중(wait_for_standby_release로 대기)

    Raises:
        RuntimeError — Redis 미연결/retry 소진. 호출자가 degrade 처리.
    """
    # 값 == owner 토큰(bare). compare_and_delete가 "저장값 전체 == expected"로 비교하므로
    # 값에 토큰만 담아야 release(key, token)가 자기 것만 삭제한다.
    token = uuid.uuid4().hex
    try:
        acquired = await asyncio.to_thread(
            RedisManager().setnx, key, token, ttl=ttl_seconds
        )
    except (RedisUnavailableError, RedisRetryExhausted) as e:
        raise RuntimeError("Redis 미연결 — standby 불가") from e
    return token if acquired else None


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
    while True:
        # 캐시부터 확인 — winner가 빨리 끝났으면 첫 backoff 낭비 없이 즉시 반환
        result = find_cache_fn()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result

        if elapsed >= timeout:
            break

        await asyncio.sleep(delay)
        elapsed += delay
        delay = min(delay * _BACKOFF_MULTIPLIER, _BACKOFF_CAP)

    logger.warning(
        "standby 대기 timeout(%d초) 초과 — 재선점/직접 업로드로 이관. key=%s",
        timeout, key,
    )
    return None


async def release_standby(key: str, token: str) -> None:
    """owner 토큰 일치 시에만 standby 삭제(compare_and_delete). finally 용, 실패는 삼킴.

    무조건 DEL이 아니라 CAD를 써서, TTL 만료 후 남이 재선점한 키를 뒤늦게 지우는 것을
    막는다. Redis 미연결/토큰 불일치(False)는 무해 — 남은 키는 TTL로 자연 정리.
    """
    try:
        await asyncio.to_thread(RedisManager().compare_and_delete, key, token)
    except Exception:
        logger.exception("standby release 실패(무시, TTL 정리): key=%s", key)
