from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_handler import call as sync_call
from llm_handler import stream as sync_stream
from llm_handler.aio import call as aio_call
from llm_handler.aio import stream as aio_stream
from llm_handler.models import FileRef, LLMResponse, Part, StreamChunk

from .checksum import file_checksum as _compute_checksum
from .done_check import DoneCheckResult, done_check
from .meta import build_meta
from .store import save_result

logger = logging.getLogger(__name__)

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
    file_meta: list[dict] | None


# ── file_paths 처리 ───────────────────────────────────────────

def _needs_file_paths(llm_input: dict) -> bool:
    """contents에 FileRef/Part가 있거나 cached_content가 있으면 True."""
    contents = llm_input.get("contents")
    if isinstance(contents, list):
        if any(isinstance(c, (FileRef, Part)) for c in contents):
            return True
    return bool(llm_input.get("cached_content"))


def _resolve_file_meta(
    llm_input: dict, file_paths: list[str] | None,
) -> list[dict] | None:
    """file_paths → file_meta 변환 + 검증."""
    if not _needs_file_paths(llm_input):
        return None

    if not file_paths:
        raise ValueError(
            "contents에 FileRef/Part가 포함되었거나 cached_content가 사용된 경우 "
            "file_paths를 반드시 제공해야 합니다."
        )

    return [_compute_checksum(p) for p in file_paths]


# ── prepare / finalize ─────────────────────────────────────────

def _prepare(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
) -> _Prepared:
    """캐시 조회 + 메타 준비. LLM 호출 전까지의 모든 로직."""
    meta = build_meta(llm_input)
    requested_at = datetime.now(UTC)

    # ── file_paths → file_meta 변환 + 검증 ──────────────────
    if cache_mode or save_mode:
        file_meta = _resolve_file_meta(llm_input, file_paths)
    else:
        # 캐시/저장 둘 다 꺼져있으면 checksum 불필요, warning만
        file_meta = None
        if _needs_file_paths(llm_input) and not file_paths:
            logger.warning(
                "파일이 포함된 요청이지만 file_paths가 없습니다. "
                "cache_mode 또는 save_mode 활성화 시 file_paths가 필요합니다."
            )

    # keying용 입력: file_meta가 있으면 checksum만 주입
    if file_meta:
        keying_input = {**llm_input, "file_checksums": [m["checksum"] for m in file_meta]}
    else:
        keying_input = llm_input

    # ── ① cache_mode=F, save_mode=F → 캐시/키 불필요 ────────
    if not cache_mode and not save_mode:
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=False,
            ttl_seconds=ttl_seconds, debug=debug,
            requested_at=requested_at,
            dc=None, cached_output=None,
            route_reason="cache_off_save_off" if debug else None,
            file_meta=None,
        )

    # ── ② cache_mode=F, save_mode=T → 키만 생성 ─────────────
    if not cache_mode:
        dc = done_check(keying_input, lookup_cache=False, database=database)
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=True,
            ttl_seconds=ttl_seconds, debug=debug,
            requested_at=requested_at,
            dc=dc, cached_output=None,
            route_reason="cache_off_save_on" if debug else None,
            file_meta=file_meta,
        )

    # ── ③④ cache_mode=T → 조회 → hit/miss ───────────────────
    dc = done_check(keying_input, lookup_cache=True, database=database)

    if dc.cached is not None:
        cached_raw = dc.cached.get("result", {})
        try:
            cached_result = LLMResponse.model_validate(cached_raw)
        except Exception:
            logger.warning(
                "캐시 결과 역직렬화 실패: cache_key=%s — 캐시 무시하고 LLM 재호출",
                dc.cache_key[:16] + "...",
                exc_info=True,
            )
            return _Prepared(
                llm_input=llm_input, meta=meta,
                cache_mode=True, save_mode=save_mode,
                ttl_seconds=ttl_seconds, debug=debug,
                requested_at=requested_at,
                dc=dc, cached_output=None,
                route_reason="cache_deserialize_error" if debug else None,
                file_meta=file_meta,
            )

        cached_meta: dict[str, Any] = {
            "source": "cache",
            "cache_hit": True,
            "cache_key": dc.cache_key,
            "metadata": dc.cached.get("metadata", {}),
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
            file_meta=file_meta,
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
        file_meta=file_meta,
    )


def _build_live_meta(prep: _Prepared) -> dict[str, Any]:
    """live 호출의 meta dict를 구성한다."""
    meta: dict[str, Any] = {
        "source": "live",
        "cache_hit": False,
        "cache_key": prep.dc.cache_key if prep.dc else None,
        "metadata": prep.meta,
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
        try:
            save_result(
                prep.dc.cache_key, prep.dc.canonical_json,
                llm_result, prep.meta, prep.dc.collection,
                database=database, ttl_seconds=prep.ttl_seconds,
                requested_at=prep.requested_at,
                file_meta=prep.file_meta,
            )
        except Exception:
            logger.exception(
                "LLM 결과 저장 실패: cache_key=%s — 결과는 정상 반환하되 캐시 미저장",
                prep.dc.cache_key[:16] + "...",
            )
    return llm_result, _build_live_meta(prep)


# ── stream finalize ────────────────────────────────────────────

def _finalize_stream(
    prep: _Prepared, chunks: list[StreamChunk], *, database: str | None = None,
) -> None:
    """Stream 완료 후 누적 청크를 조립하여 MDB에 저장."""
    if not prep.save_mode or prep.dc is None:
        return
    if not chunks:
        logger.warning(
            "스트림 완료 시 청크가 0개: cache_key=%s — 빈 결과는 저장하지 않음",
            prep.dc.cache_key[:16] + "...",
        )
        return

    full_text = "".join((c.text or "") for c in chunks)
    last = chunks[-1]

    # function_calls: 비-None인 청크 중 마지막 값을 채택
    function_calls = None
    for c in chunks:
        if c.function_calls is not None:
            function_calls = c.function_calls

    assembled: dict = {
        "text": full_text,
        "model": prep.llm_input.get("model", "unknown"),
        "usage": last.usage.model_dump() if last.usage else None,
        "finish_reason": last.finish_reason,
        "request_id": "",
        "source": "stream",
    }
    if function_calls is not None:
        assembled["function_calls"] = [fc.model_dump() for fc in function_calls]

    try:
        save_result(
            prep.dc.cache_key, prep.dc.canonical_json,
            assembled, prep.meta, prep.dc.collection,
            database=database, ttl_seconds=prep.ttl_seconds,
            requested_at=prep.requested_at,
            file_meta=prep.file_meta,
        )
    except Exception:
        logger.exception(
            "스트림 결과 저장 실패: cache_key=%s — 캐시 미저장",
            prep.dc.cache_key[:16] + "...",
        )


def _to_stream_chunk(response: LLMResponse) -> StreamChunk:
    """LLMResponse를 StreamChunk 1개로 변환한다 (캐시 히트 시 사용)."""
    return StreamChunk(
        text=response.text,
        finish_reason=response.finish_reason,
        usage=response.usage,
        function_calls=response.function_calls,
    )


# ── Public API ─────────────────────────────────────────────────

def llm_request(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). (LLMResponse, meta) 튜플 반환."""
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
    )
    if prep.cached_output:
        return prep.cached_output
    res = sync_call(**prep.llm_input)
    return _finalize(prep, res, database=database)


async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (LLMResponse, meta) 튜플 반환."""
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
    )
    if prep.cached_output:
        return prep.cached_output
    res = await aio_call(**prep.llm_input)
    return _finalize(prep, res, database=database)


def llm_request_stream(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
) -> tuple[Iterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). 항상 (Iterator[StreamChunk], meta) 튜플 반환."""
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
    )
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
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
) -> tuple[AsyncIterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (async). 항상 (AsyncIterator[StreamChunk], meta) 튜플 반환."""
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
    )
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
