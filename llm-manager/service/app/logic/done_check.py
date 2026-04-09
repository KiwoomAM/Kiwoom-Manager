"""done_check: canonicalize → hash → (optional) cache lookup"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.logic.keying import canonicalize_input, make_cache_key
from app.logic.store import cache_get

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "agent_llm"


@dataclass(frozen=True)
class DoneCheckResult:
    collection: str
    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_error: bool


def done_check(
    llm_input: dict,
    *,
    lookup_cache: bool = True,
    database: str | None = None,
) -> DoneCheckResult:
    canonical_json = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical_json)

    collection = DEFAULT_COLLECTION

    cached = None
    cache_error = False
    if lookup_cache:
        try:
            cached = cache_get(cache_key, collection, database=database)
        except Exception:
            logger.exception(
                "캐시 조회 실패: cache_key=%s, collection=%s — LLM 직접 호출로 대체",
                cache_key[:16] + "...",
                collection,
            )
            cached = None
            cache_error = True

    return DoneCheckResult(
        collection=collection,
        cache_key=cache_key,
        canonical_json=canonical_json,
        cached=cached,
        cache_error=cache_error,
    )
