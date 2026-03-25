# -*- coding: utf-8 -*-
"""done_check: canonicalize → hash → (optional) cache lookup"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .keying import canonicalize_input, make_cache_key
from .store import cache_get

DEFAULT_COLLECTION = "agent_llm"


@dataclass(frozen=True)
class DoneCheckResult:
    collection: str
    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_error: bool


def done_check(
    state: dict,
    *,
    lookup_cache: bool = True,
    database: str | None = None,
) -> DoneCheckResult:
    canonical_json = canonicalize_input(state)
    cache_key = make_cache_key(canonical_json)

    collection = DEFAULT_COLLECTION

    cached = None
    cache_error = False
    if lookup_cache:
        try:
            cached = cache_get(cache_key, collection, database=database)
        except Exception:
            cached = None
            cache_error = True

    return DoneCheckResult(
        collection=collection,
        cache_key=cache_key,
        canonical_json=canonical_json,
        cached=cached,
        cache_error=cache_error,
    )
