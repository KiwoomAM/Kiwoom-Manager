from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_handler import call as sync_call
from llm_handler import stream as sync_stream
from llm_handler.aio import call as aio_call
from llm_handler.aio import stream as aio_stream
from llm_handler.models import FileRef, LLMResponse, Part, StreamChunk

from app.logic.done_check import DoneCheckResult, done_check
from app.logic.file_handler import resolve_files
from app.logic.meta import build_meta
from app.logic.store import save_result

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
    file_paths: list[str] | None
    context_cache_ttl: int | None


# ── prepare / finalize ─────────────────────────────────────────

def _prepare(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    file_meta: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> _Prepared:
    """캐시 조회 + 메타 준비. LLM 호출 전까지의 모든 로직.

    서버 모드에서는 file_meta를 직접 전달받는다 (SDK에서 checksum 계산 완료).
    """
    meta = build_meta(llm_input)
    requested_at = datetime.now(UTC)

    # ── file_meta 처리 ──────────────────────────────────────
    if cache_mode or save_mode:
        pass  # file_meta는 호출자가 직접 전달
    else:
        file_meta = None

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
            file_paths=file_paths, context_cache_ttl=context_cache_ttl,
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
            file_paths=file_paths, context_cache_ttl=context_cache_ttl,
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
                file_paths=file_paths, context_cache_ttl=context_cache_ttl,
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
            file_paths=file_paths, context_cache_ttl=context_cache_ttl,
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
        file_paths=file_paths, context_cache_ttl=context_cache_ttl,
    )


def _build_call_input(llm_input: dict, resolved: object) -> dict:
    """resolve_files 결과를 llm_input에 반영한 호출용 dict를 만든다."""
    from .file_handler import ResolvedFiles

    assert isinstance(resolved, ResolvedFiles)
    call_input = {**llm_input}

    if resolved.cached_content:
        # context cache 사용 → cached_content 설정, 파일 관련 contents 제거
        call_input["cached_content"] = resolved.cached_content
        # contents에서 기존 FileRef/Part 제거, 텍스트만 유지
        contents = call_input.get("contents")
        if isinstance(contents, list):
            call_input["contents"] = [
                c for c in contents if isinstance(c, str)
            ]
    elif resolved.contents_parts:
        # inline/Files API → contents에 Part 추가 (파일 먼저, 텍스트 뒤)
        contents = call_input.get("contents", "")
        if isinstance(contents, str):
            contents = [contents] if contents else []
        elif isinstance(contents, list):
            # 기존 FileRef/Part 제거, 텍스트만 유지
            contents = [c for c in contents if isinstance(c, str)]
        call_input["contents"] = list(resolved.contents_parts) + contents

    # keying용 필드 제거 (llm_handler.call에 전달하면 안 됨)
    call_input.pop("file_checksums", None)

    return call_input


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
            logger.info(
                "MDB 저장 완료: database=%s, collection=%s, cache_key=%s",
                database or os.getenv("MONGODB_DATABASE", "kw_ai"),
                prep.dc.collection,
                prep.dc.cache_key[:16] + "...",
            )
        except Exception:
            logger.exception(
                "MDB 저장 실패: database=%s, collection=%s, cache_key=%s",
                database or os.getenv("MONGODB_DATABASE", "kw_ai"),
                prep.dc.collection,
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
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). (LLMResponse, meta) 튜플 반환.

    Note:
        file_handler(resolve_files)는 async 전용. sync에서는 file_paths에 의한
        자동 업로드/캐싱이 동작하지 않음. async 함수(llm_request_async) 사용 권장.
    """
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        return prep.cached_output
    res = sync_call(**prep.llm_input)
    return _finalize(prep, res, database=database)


async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    file_meta: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (LLMResponse, meta) 튜플 반환.

    Args:
        file_meta: SDK에서 계산한 checksum 메타 리스트 (서버 모드).
        context_cache_ttl: context cache TTL (초). None이면 context caching 안 함.
            같은 파일로 시간당 4회 이상 호출할 때만 권장.
    """
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        file_meta=file_meta,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        return prep.cached_output

    # ── file_paths가 있으면 resolve_files → llm_input 조립 ────
    call_input = prep.llm_input
    if prep.file_meta and prep.file_paths:
        resolved = await resolve_files(
            prep.file_meta,
            prep.file_paths,
            model=prep.llm_input.get("model"),
            system_instruction=prep.llm_input.get("system"),
            context_cache_ttl=prep.context_cache_ttl,
        )
        call_input = _build_call_input(prep.llm_input, resolved)

    res = await aio_call(**call_input)
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
    context_cache_ttl: int | None = None,
) -> tuple[Iterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). 항상 (Iterator[StreamChunk], meta) 튜플 반환.

    Note:
        file_handler(resolve_files)는 async 전용. sync에서는 file_paths에 의한
        자동 업로드/캐싱이 동작하지 않음. async 함수(llm_request_stream_async) 사용 권장.
    """
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
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
    context_cache_ttl: int | None = None,
) -> tuple[AsyncIterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (async). 항상 (AsyncIterator[StreamChunk], meta) 튜플 반환.

    Args:
        context_cache_ttl: context cache TTL (초). None이면 context caching 안 함.
    """
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        response, meta = prep.cached_output

        async def _cached() -> AsyncIterator[StreamChunk]:
            yield _to_stream_chunk(response)

        return _cached(), meta

    # ── file_paths가 있으면 resolve_files → llm_input 조립 ────
    call_input = prep.llm_input
    if prep.file_meta and prep.file_paths:
        resolved = await resolve_files(
            prep.file_meta,
            prep.file_paths,
            model=prep.llm_input.get("model"),
            system_instruction=prep.llm_input.get("system"),
            context_cache_ttl=prep.context_cache_ttl,
        )
        call_input = _build_call_input(prep.llm_input, resolved)

    async def _stream() -> AsyncIterator[StreamChunk]:
        accumulated: list[StreamChunk] = []
        async for chunk in aio_stream(**call_input):
            accumulated.append(chunk)
            yield chunk
        _finalize_stream(prep, accumulated, database=database)

    return _stream(), _build_live_meta(prep)
