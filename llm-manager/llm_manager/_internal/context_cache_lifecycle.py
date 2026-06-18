"""Context Cache lifecycle — expire 처리.

resolver 의 생성 로직과 분리. TTL 단축 (Gemini update + Redis EXPIRE) 담당.

청구 동작: Gemini caches.update 는 새 TTL 로 prorate 청구 (delete 와 다름).
공식 forum thread #81267. design: docs/20.context-cache-expire.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, TypedDict

from llm_manager._internal.file_processing import cache_store
from llm_manager._internal.gemini_errors import is_gemini_404
from llm_manager._internal.handler_client import aio as llm_aio
from llm_manager.models import ExpireFailure, ExpireResult

logger = logging.getLogger(__name__)


class _ExpireOutcome(TypedDict, total=False):
    cache_id: str
    status: Literal["expired", "failed"]
    error: str


async def expire_caches(
    cache_ids: list[str],
    grace_seconds: int,
) -> ExpireResult:
    """다중 cache_id 동시 expire — partial failure 허용."""
    results = await asyncio.gather(
        *[_expire_one(cid, grace_seconds) for cid in cache_ids],
    )
    expired = [r["cache_id"] for r in results if r["status"] == "expired"]
    failed = [
        ExpireFailure(cache_id=r["cache_id"], error=r["error"])
        for r in results if r["status"] == "failed"
    ]
    return ExpireResult(expired=expired, failed=failed)


async def _expire_one(cache_id: str, grace_seconds: int) -> _ExpireOutcome:
    # ① Redis 조회 (cache_id == hash_key)
    try:
        doc = cache_store.find_context_cache(cache_id)
    except Exception as e:
        logger.exception("Redis 조회 실패: cache_id=%s", cache_id[:16])
        return {
            "cache_id": cache_id,
            "status": "failed",
            "error": f"캐시 정보 조회에 실패했습니다. 잠시 후 다시 시도하세요. ({type(e).__name__})",
        }

    if not doc:
        # idempotent — 이미 정리됨/만료됨
        return {"cache_id": cache_id, "status": "expired"}

    name = doc["cached_content_name"]

    # ② Gemini update
    try:
        await llm_aio.update_cache(name, grace_seconds)
    except Exception as e:
        if is_gemini_404(e):
            return {"cache_id": cache_id, "status": "expired"}
        logger.exception("Gemini update_cache 실패: cache_id=%s", cache_id[:16])
        return {
            "cache_id": cache_id,
            "status": "failed",
            "error": f"캐시 만료 처리에 실패했습니다. 잠시 후 다시 시도하세요. ({type(e).__name__})",
        }

    # ③ Redis TTL 단축 (실패해도 expired — Gemini 비용은 단축됨)
    try:
        cache_store.shrink_context_cache_ttl(cache_id, grace_seconds)
    except Exception:
        logger.exception(
            "Redis TTL 단축 실패 (Gemini는 단축됨): cache_id=%s", cache_id[:16],
        )

    return {"cache_id": cache_id, "status": "expired"}


async def extend_caches(
    cache_ids: list[str],
    ttl: int,
) -> ExpireResult:
    """다중 cache_id 동시 연장(update) — partial failure 허용.

    `expire_caches` 의 거울. Gemini `caches.update(ttl)` 로 수명을 ttl 로 연장하고
    Redis TTL 도 맞춘다. expire 와 달리 Redis 부재·Gemini 404 는 **failed**(연장 대상
    없음). docs/26 §2.4/§4.4. (반환형은 `ExpireResult` 재사용 — `expired`=연장 성공.)
    """
    results = await asyncio.gather(
        *[_extend_one(cid, ttl) for cid in cache_ids],
    )
    expired = [r["cache_id"] for r in results if r["status"] == "expired"]
    failed = [
        ExpireFailure(cache_id=r["cache_id"], error=r["error"])
        for r in results if r["status"] == "failed"
    ]
    return ExpireResult(expired=expired, failed=failed)


async def _extend_one(cache_id: str, ttl: int) -> _ExpireOutcome:
    """_expire_one 의 거울 — 부재·404 를 failed 로 본다 (연장 대상이 없으므로)."""
    # ① Redis 조회 (cache_id == hash_key)
    try:
        doc = cache_store.find_context_cache(cache_id)
    except Exception as e:
        logger.exception("Redis 조회 실패: cache_id=%s", cache_id[:16])
        return {
            "cache_id": cache_id,
            "status": "failed",
            "error": f"캐시 정보 조회에 실패했습니다. 잠시 후 다시 시도하세요. ({type(e).__name__})",
        }

    if not doc:
        # expire 와 반대 — 연장할 대상이 없으면 실패 (cached_content 이름도 못 얻음)
        return {
            "cache_id": cache_id,
            "status": "failed",
            "error": "연장할 캐시를 찾을 수 없습니다 (이미 만료/삭제되었거나 잘못된 id).",
        }

    name = doc["cached_content_name"]

    # ② Gemini update (404 → 캐시 사라짐 → 연장 불가 → failed)
    try:
        await llm_aio.update_cache(name, ttl)
    except Exception as e:
        if is_gemini_404(e):
            return {
                "cache_id": cache_id,
                "status": "failed",
                "error": "연장할 캐시가 Gemini 에 없습니다 (이미 만료/삭제됨).",
            }
        logger.exception("Gemini update_cache 실패: cache_id=%s", cache_id[:16])
        return {
            "cache_id": cache_id,
            "status": "failed",
            "error": f"캐시 연장 처리에 실패했습니다. 잠시 후 다시 시도하세요. ({type(e).__name__})",
        }

    # ③ Redis TTL 연장 (실패해도 expired — Gemini 는 이미 연장됨)
    try:
        cache_store.set_context_cache_ttl(cache_id, ttl)
    except Exception:
        logger.exception(
            "Redis TTL 연장 실패 (Gemini는 연장됨): cache_id=%s", cache_id[:16],
        )

    return {"cache_id": cache_id, "status": "expired"}
