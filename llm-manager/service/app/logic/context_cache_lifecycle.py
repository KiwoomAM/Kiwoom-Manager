"""Context Cache lifecycle — expire 처리.

resolver 의 생성 로직과 분리. TTL 단축 (Gemini update + Redis EXPIRE) 담당.

청구 동작: Gemini caches.update 는 새 TTL 로 prorate 청구 (delete 와 다름).
공식 forum thread #81267. design: docs/20.context-cache-expire.md
"""

from __future__ import annotations

import asyncio
import logging

from llm_handler import aio as llm_aio

from app.logic.file_processing import cache_store
from app.logic.runner import is_gemini_404
from app.models.responses import ExpireContextCacheResponse, ExpireFailure

logger = logging.getLogger(__name__)


async def expire_caches(
    cache_ids: list[str],
    grace_seconds: int,
) -> ExpireContextCacheResponse:
    """다중 cache_id 동시 expire — partial failure 허용."""
    results = await asyncio.gather(
        *[_expire_one(cid, grace_seconds) for cid in cache_ids],
    )
    expired = [r["cache_id"] for r in results if r["status"] == "expired"]
    failed = [
        ExpireFailure(cache_id=r["cache_id"], error=r["error"])
        for r in results if r["status"] == "failed"
    ]
    return ExpireContextCacheResponse(expired=expired, failed=failed)


async def _expire_one(cache_id: str, grace_seconds: int) -> dict:
    # ① Redis 조회 (cache_id == hash_key)
    try:
        doc = cache_store.find_context_cache(cache_id)
    except Exception as e:
        logger.exception("Redis 조회 실패: cache_id=%s", cache_id[:16])
        return {"cache_id": cache_id, "status": "failed", "error": str(e)}

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
        return {"cache_id": cache_id, "status": "failed", "error": str(e)}

    # ③ Redis TTL 단축 (실패해도 expired — Gemini 비용은 단축됨)
    try:
        cache_store.shrink_context_cache_ttl(cache_id, grace_seconds)
    except Exception:
        logger.exception(
            "Redis TTL 단축 실패 (Gemini는 단축됨): cache_id=%s", cache_id[:16],
        )

    return {"cache_id": cache_id, "status": "expired"}
