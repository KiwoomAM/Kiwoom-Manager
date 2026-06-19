"""done_check: canonicalize → hash → (optional) cache lookup"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from llm_manager._internal.keying import (
    build_llm_input_subset,
    canonicalize_input,
    canonicalize_llm_input,
    make_cache_key,
)
from llm_manager._internal.store import cache_get

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "agent_llm"


@dataclass(frozen=True)
class DoneCheckResult:
    collection: str
    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_error: bool


# ── [임시 — 마이그레이션 전환기 dual-key 조회] ─────────────────────────────────
# 호출 라벨을 cache_key 에서 제거하면서, 아직 마이그레이션되지 않은 "옛 키"(service_name
# 포함) 엔트리가 새 키로는 miss 난다. 전환기 동안만, 새 키 miss 시 옛 키로 한 번 더 조회한다.
#   옛 키 = subset 에 service_name(=env 값)을 다시 넣어 해시 (값이 옛 입력과 같을 때만 hit).
# ★ 최종 마이그레이션 완료 후 이 헬퍼와 done_check 의 호출부를 함께 제거할 것.
def _legacy_cache_get(
    llm_input: dict, collection: str, database: str | None,
) -> dict[str, Any] | None:
    service_name = llm_input.get("service_name")
    if not service_name:
        return None
    subset = build_llm_input_subset(llm_input)
    subset["service_name"] = service_name
    legacy_key = make_cache_key(canonicalize_llm_input(subset))
    return cache_get(legacy_key, collection, database=database)
# ── [임시 끝] ─────────────────────────────────────────────────────────────────


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
            # ① 새 키(라벨 없음)로 조회
            cached = cache_get(cache_key, collection, database=database)
            # ② [임시] miss 면 옛 키(service_name 포함)로 한 번 더 — 마이그레이션 후 제거
            if cached is None:
                cached = _legacy_cache_get(llm_input, collection, database)
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
