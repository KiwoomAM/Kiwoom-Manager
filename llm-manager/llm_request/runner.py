from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_handler import call as sync_call, stream as sync_stream
from llm_handler.aio import call as aio_call, stream as aio_stream
from llm_handler.models import LLMResponse, StreamChunk

from .store import save_result
from .meta import build_meta
from .done_check import DoneCheckResult, done_check

 
# ── 내부 데이터 ────────────────────────────────────────────────

@dataclass
class _Prepared:
    llm_input: dict
    meta: dict
    cache_mode: bool
    save_mode: bool
    ttl_seconds: int | None
    debug: bool
    requested_at: datetime
    dc: DoneCheckResult | None
    cached_output: tuple[LLMResponse, dict[str, Any]] | None
    route_reason: str | None


# ── prepare / finalize ─────────────────────────────────────────

def _prepare(state: dict, *, database: str | None = None) -> _Prepared:
    """캐시 조회 + 메타 준비. LLM 호출 전까지의 모든 로직."""
    cache_mode: bool = state.get("cache_mode", True)
    save_mode: bool = state.get("save_mode", True)
    ttl_seconds: int | None = state.get("ttl_seconds")
    debug: bool = state.get("debug", False)

    llm_input: dict = state.get("llm_input", {})
    meta = build_meta(state)
    requested_at = datetime.now(UTC)

    # ── ① cache_mode=F, save_mode=F → 캐시/키 불필요 ────────
    if not cache_mode and not save_mode:
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=False,
            ttl_seconds=ttl_seconds, debug=debug,
            requested_at=requested_at,
            dc=None, cached_output=None,
            route_reason="cache_off_save_off" if debug else None,
        )

    # ── ② cache_mode=F, save_mode=T → 키만 생성 ─────────────
    if not cache_mode:
        dc = done_check(state, lookup_cache=False, database=database)
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=True,
            ttl_seconds=ttl_seconds, debug=debug,
            requested_at=requested_at,
            dc=dc, cached_output=None,
            route_reason="cache_off_save_on" if debug else None,
        )

    # ── ③④ cache_mode=T → 조회 → hit/miss ───────────────────
    dc = done_check(state, lookup_cache=True, database=database)

    if dc.cached is not None:
        cached_raw = dc.cached.get("result", {})
        try:
            cached_result = LLMResponse.model_validate(cached_raw)
        except Exception:
            cached_result = cached_raw

        cached_meta: dict[str, Any] = {
            "source": "cache",
            "cache_hit": True,
            "cache_key": dc.cache_key,
            "meta": dc.cached.get("meta", {}),
            "collection": dc.collection,
        }
        if debug:
            cached_meta["route_reason"] = "cache_hit"

        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=True, save_mode=save_mode,
            ttl_seconds=ttl_seconds, debug=debug,
            requested_at=requested_at,
            dc=dc, cached_output=(cached_result, cached_meta),
            route_reason="cache_hit" if debug else None,
        )

    # cache miss (또는 cache error)
    route_reason = "cache_error_fallback" if dc.cache_error else "cache_miss"
    return _Prepared(
        llm_input=llm_input, meta=meta,
        cache_mode=True, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug,
        requested_at=requested_at,
        dc=dc, cached_output=None,
        route_reason=route_reason if debug else None,
    )


def _build_live_meta(prep: _Prepared) -> dict[str, Any]:
    """live 호출의 meta dict를 구성한다."""
    meta: dict[str, Any] = {
        "source": "live",
        "cache_hit": False,
        "cache_key": prep.dc.cache_key if prep.dc else None,
        "meta": prep.meta,
        "collection": prep.dc.collection if prep.dc else None,
    }
    if prep.route_reason:
        meta["route_reason"] = prep.route_reason
    return meta


def _finalize(
    prep: _Prepared, llm_result: LLMResponse, *, database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """LLM 응답 저장 + (LLMResponse, meta) 튜플 반환."""
    if prep.save_mode and prep.dc is not None:
        save_result(
            prep.dc.cache_key, prep.dc.canonical_json,
            llm_result, prep.meta, prep.dc.collection,
            database=database, ttl_seconds=prep.ttl_seconds,
            requested_at=prep.requested_at,
        )
    return llm_result, _build_live_meta(prep)


# ── stream finalize ────────────────────────────────────────────

def _finalize_stream(
    prep: _Prepared, chunks: list[StreamChunk], *, database: str | None = None,
) -> None:
    """Stream 완료 후 누적 청크를 조립하여 MDB에 저장."""
    if not prep.save_mode or prep.dc is None:
        return

    full_text = "".join(c.text for c in chunks)
    last = chunks[-1] if chunks else None

    assembled = {
        "text": full_text,
        "model": prep.llm_input.get("model", "unknown"),
        "usage": last.usage.model_dump() if (last and last.usage) else None,
        "finish_reason": last.finish_reason if last else None,
        "request_id": "",
        "source": "stream",
    }

    save_result(
        prep.dc.cache_key, prep.dc.canonical_json,
        assembled, prep.meta, prep.dc.collection,
        database=database, ttl_seconds=prep.ttl_seconds,
        requested_at=prep.requested_at,
    )


def _to_stream_chunk(response: LLMResponse) -> StreamChunk:
    """LLMResponse를 StreamChunk 1개로 변환한다 (캐시 히트 시 사용)."""
    return StreamChunk(
        text=response.text,
        finish_reason=getattr(response, "finish_reason", None),
        usage=getattr(response, "usage", None),
    )


# ── Public API ─────────────────────────────────────────────────

def llm_request(
    state: dict, *, database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). (LLMResponse, meta) 튜플 반환."""
    prep = _prepare(state, database=database)
    if prep.cached_output:
        return prep.cached_output
    res = sync_call(**prep.llm_input)
    return _finalize(prep, res, database=database)


async def llm_request_async(
    state: dict, *, database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (LLMResponse, meta) 튜플 반환."""
    prep = _prepare(state, database=database)
    if prep.cached_output:
        return prep.cached_output
    res = await aio_call(**prep.llm_input)
    return _finalize(prep, res, database=database)


def llm_request_stream(
    state: dict, *, database: str | None = None,
) -> tuple[Iterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). 항상 (Iterator[StreamChunk], meta) 튜플 반환."""
    prep = _prepare(state, database=database)
    if prep.cached_output:
        response, meta = prep.cached_output

        def _cached() -> Iterator[StreamChunk]:
            yield _to_stream_chunk(response)

        return _cached(), meta

    def _stream() -> Iterator[StreamChunk]:
        accumulated: list[StreamChunk] = []
        for chunk in sync_stream(**prep.llm_input):
            accumulated.append(chunk)
            yield chunk
        _finalize_stream(prep, accumulated, database=database)

    return _stream(), _build_live_meta(prep)


async def llm_request_stream_async(
    state: dict, *, database: str | None = None,
) -> tuple[AsyncIterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (async). 항상 (AsyncIterator[StreamChunk], meta) 튜플 반환."""
    prep = _prepare(state, database=database)
    if prep.cached_output:
        response, meta = prep.cached_output

        async def _cached() -> AsyncIterator[StreamChunk]:
            yield _to_stream_chunk(response)

        return _cached(), meta

    async def _stream() -> AsyncIterator[StreamChunk]:
        accumulated: list[StreamChunk] = []
        async for chunk in aio_stream(**prep.llm_input):
            accumulated.append(chunk)
            yield chunk
        _finalize_stream(prep, accumulated, database=database)

    return _stream(), _build_live_meta(prep)
