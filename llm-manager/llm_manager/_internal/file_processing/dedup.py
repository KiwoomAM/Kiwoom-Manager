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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis_manager import (
    RedisManager,
    RedisRetryExhausted,
    RedisUnavailableError,
)

from llm_manager.exceptions import LLMManagerError

logger = logging.getLogger(__name__)


# ── backoff 파라미터 (§4) ────────────────────────────────────

_BACKOFF_INITIAL = 0.5
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_CAP = 10.0


# ── 실패 기록 TTL (계층별, docs/31 §3-1) ──────────────────────
# 폴링 cap(_BACKOFF_CAP)보다 커야 만료 전에 모든 대기자가 기록을 본다.
# files_api/context_cache 는 재작업이 비싸 60초, agent_llm 은 일시적 실패가 많아 15초.
_FAILED_TTL_SECONDS = {"files_api": 60, "context_cache": 60, "agent_llm": 15}


# ── 대기 판정 (verdict, docs/31 §3-4) ─────────────────────────


@dataclass
class WaitResult:
    """wait_for_standby_release 의 판정.

    kind:
        "cached" — leader 성공, 캐시 HIT. `data` 에 캐시 dict.
        "failed" — leader 실패 기록 발견. `error` 에 재구성된 예외 (호출자가 raise).
        "lead"   — standby 사라짐/timeout 후 SETNX 재획득 성공 → 자기가 작업.
    """

    kind: str
    data: Any = None
    error: Exception | None = None


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


def _make_failed_key(prefix: str, key: str) -> str:
    """실패 기록(negative cache) full Redis 키. standby 와 같은 네임스페이스 규칙."""
    if prefix in ("files_api", "context_cache"):
        return f"file_cache:{prefix}_failed:{key}"
    elif prefix == "agent_llm":
        return f"agent_llm_failed:{key}"
    else:
        raise ValueError(f"Unknown failed prefix: {prefix}")


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


async def record_failure(prefix: str, key: str, error: Exception) -> None:
    """leader 실패를 negative cache 로 기록한다 (docs/31 §3-1).

    대기자들이 이 기록을 보고 **재시도 없이** 같은 에러로 즉시 실패한다 — leader 하나의
    실패가 27개의 동시 재작업(herd)으로 번지던 것을 막는다.

    호출 순서가 중요하다: leader 는 이 기록을 남긴 **뒤에** standby 를 해제해야 한다.
    반대로 하면 그 사이에 깨어난 대기자가 기록을 못 보고 자기가 작업을 시작한다.

    원본 예외의 관찰 가능한 계약(message / status_code / error_type)만 보존한다 —
    클래스 생성자가 제각각이라(`PdfPageTooLargeError(page, size_mb)`) 타입 재구성은
    취약하고, 소비자는 어차피 이 세 값으로 분기·로깅한다(docs/31 §3-3).

    Redis 장애 시 예외는 삼킨다 — 기록에 실패해도 SETNX 폴백이 herd 를 막으므로
    원본 예외 전파를 방해하지 않는 게 낫다.
    """
    info = {
        "class": type(error).__name__,  # 진단용 — 재구성에는 안 쓴다
        "message": str(error),  # verbatim, 마스킹 없음
        "status_code": getattr(error, "status_code", 502),
        "error_type": getattr(error, "error_type", "leader_failed"),
    }
    try:
        await asyncio.to_thread(
            RedisManager().set,
            _make_failed_key(prefix, key),
            json.dumps(info),
            ttl=_FAILED_TTL_SECONDS[prefix],
        )
    except Exception:
        logger.exception(
            "실패 기록 저장 실패 (무시 — SETNX 폴백이 herd 를 막는다): prefix=%s, key=%s",
            prefix, key[:16] if key else "",
        )


async def _find_failure(prefix: str, key: str) -> dict | None:
    """실패 기록 조회. 없거나 파손이면 None (정상 경로로 진행)."""
    try:
        raw = await asyncio.to_thread(RedisManager().get, _make_failed_key(prefix, key))
    except Exception:
        logger.exception("실패 기록 조회 실패 (무시): prefix=%s", prefix)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("실패 기록 파손 — 무시: prefix=%s, key=%s", prefix, key[:16])
        return None


def _reconstruct_error(info: dict) -> Exception:
    """실패 기록 → 대기자가 raise 할 예외.

    원본 클래스가 아니라 `LLMManagerError` 로 재구성하되 message·status_code·error_type
    을 그대로 실어, 소비자가 보는 계약이 원본과 동일하게 한다 (docs/31 §3-3).
    """
    return LLMManagerError(
        info.get("message", "선행 요청이 실패했습니다"),
        status_code=info.get("status_code", 502),
        error_type=info.get("error_type", "leader_failed"),
    )


async def wait_for_standby_release(
    prefix: str,
    key: str,
    timeout: int,
    find_cache_fn: Callable[[], Any] | None = None,
) -> WaitResult:
    """standby 사라질 때까지 exponential backoff 대기 → 판정(WaitResult) 반환.

    standby 폴링은 RedisManager().get 사용 — v3 는 strict-read 를 지원하지 않으므로
    (read 는 항상 메모리 폴백) 대기 중 단절 시 get 이 None 을 반환하고, 호출자는
    timeout 폴백과 동일하게 자기 작업을 수행한다(graceful degradation).

    폴링 1회의 판단 순서 (docs/31 §3-2):
      1. 본 캐시 HIT           → CACHED (leader 성공)
      2. 실패 기록 존재        → FAILED (leader 실패 — 재시도 없이 즉시 전파)
      3. standby 사라짐        → **SETNX 재획득** → 성공하면 LEAD, 실패하면 계속 대기
      4. timeout               → SETNX 재획득 시도 후 LEAD (Y' Hybrid)

    3번이 핵심이다. 예전엔 여기서 곧장 `None` 을 돌려줘 **깨어난 대기자 전원이 동시에**
    자기 작업을 시작했다(herd). 이제는 깨어나면 항상 SETNX 를 거치므로 하나만 leader 가
    되고 나머지는 재대기한다 — 실패 기록이 (TTL 만료·eviction 으로) 없어도 불변식
    "동시 작업 ≤ 1" 이 유지된다.

    backoff 시퀀스: 0.5 → 1 → 2 → 4 → 8 → 10 (cap) → 10 → ...

    Args:
        prefix: "files_api" / "context_cache" / "agent_llm"
        key: 해시 값 (checksum / hash_key / cache_key)
        timeout: 전체 대기 timeout (= standby_ttl_seconds). 재획득 standby 의 TTL 로도 쓴다
            (호출부가 try_acquire_standby 에 넘기는 값과 동일).
        find_cache_fn: 본 캐시 조회 함수 (sync 또는 async callable)

    Returns:
        WaitResult — kind 가 "cached" / "failed" / "lead" 중 하나.
    """
    standby_key = _make_standby_key(prefix, key)
    delay = _BACKOFF_INITIAL
    elapsed = 0.0

    while elapsed < timeout:
        await asyncio.sleep(delay)
        elapsed += delay

        # 1. 본 캐시 재조회 (HIT면 종료 — leader 성공)
        if find_cache_fn is not None:
            result = find_cache_fn()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return WaitResult(kind="cached", data=result)

        # 2. leader 실패 기록 — 재시도 없이 원본 에러 전파
        failure = await _find_failure(prefix, key)
        if failure:
            logger.info(
                "leader 실패 기록 감지 — 재시도 없이 전파: prefix=%s, key=%s, error=%s",
                prefix, key[:16] if key else "", failure.get("class"),
            )
            return WaitResult(kind="failed", error=_reconstruct_error(failure))

        # 3. standby 폴링 — 단절 시 None 폴백(strict-read 미지원)
        if await asyncio.to_thread(RedisManager().get, standby_key) is None:
            # leader 가 기록 없이 사라짐(성공했는데 캐시 MISS / eviction / Redis 단절).
            # 곧장 자기가 일하지 않고 SETNX 부터 — 동시에 깨어나도 하나만 leader.
            if await _try_reacquire(prefix, key, timeout):
                return WaitResult(kind="lead")
            # 다른 대기자가 leader 가 됨 → 계속 대기

        # 다음 대기 간격
        delay = min(delay * _BACKOFF_MULTIPLIER, _BACKOFF_CAP)

    # timeout — Y' Hybrid (호출자가 작업). 여기서도 재획득을 먼저 시도한다.
    await _try_reacquire(prefix, key, timeout)
    logger.warning(
        "standby 대기 timeout(%d초) 초과 — 호출자가 작업 시작. "
        "standby_key=%s, 중복 발생 가능. 빈도 높으면 standby_ttl_seconds 늘리세요.",
        timeout, standby_key,
    )
    return WaitResult(kind="lead")


async def _try_reacquire(prefix: str, key: str, ttl_seconds: int) -> bool:
    """standby 재획득 시도. Redis 미연결이면 True(자기가 작업) — graceful degradation.

    try_acquire_standby 는 Redis 미연결 시 RuntimeError 를 올리는데, 대기 경로에서는
    그걸 호출자에게 터뜨리는 대신 "혼자 일한다"로 강등한다(기존 None 폴백과 동일한 취지).
    """
    try:
        return await try_acquire_standby(prefix, key, ttl_seconds)
    except RuntimeError:
        logger.warning("standby 재획득 불가(Redis 미연결) — 자기가 작업: prefix=%s", prefix)
        return True


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
