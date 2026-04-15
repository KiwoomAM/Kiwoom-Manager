from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from file_manager import FileMeta  # TODO: SDK 배포 전까지 없음. ② Phase A 검증 시 stub/실물 준비
from llm_handler.aio import call as aio_call
from llm_handler.aio import stream as aio_stream
from llm_handler.models import LLMResponse, StreamChunk

from app.logic.done_check import DoneCheckResult, done_check
from app.logic.file_handler import (
    ResolvedFiles,
    resolve_files,
    resolve_files_after_404,
)
from app.logic.file_handler.validators import (
    validate_file_sizes,
    validate_mime_types,
)
from app.logic.keying import make_file_checksum
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
    file_metas: list[FileMeta] | None
    context_cache_ttl: int | None


# ── prepare / finalize ─────────────────────────────────────────

def prepare(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> _Prepared:
    """캐시 조회 + 메타 준비. LLM 호출 전까지의 모든 로직.

    file_metas의 checksum을 keying.make_file_checksum으로 자체 계산하여
    cache_key 구성에 주입한다 (file_manager는 checksum 미제공).
    """
    meta = build_meta(llm_input)
    requested_at = datetime.now(UTC)

    # ── file_metas 처리 ─────────────────────────────────────
    if not (cache_mode or save_mode):
        file_metas = None

    # keying용 입력: file_metas가 있으면 checksum 리스트로 변환 후 주입
    if file_metas:
        checksums = [make_file_checksum(m) for m in file_metas]
        keying_input = {**llm_input, "file_checksums": checksums}
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
            file_metas=None,
            context_cache_ttl=context_cache_ttl,
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
            file_metas=file_metas,
            context_cache_ttl=context_cache_ttl,
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
                file_metas=file_metas,
                context_cache_ttl=context_cache_ttl,
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
            file_metas=file_metas,
            context_cache_ttl=context_cache_ttl,
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
        file_metas=file_metas,
        context_cache_ttl=context_cache_ttl,
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


def finalize(
    prep: _Prepared, llm_result: LLMResponse, *, database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """LLM 응답 저장 + (LLMResponse, meta) 튜플 반환."""
    if prep.save_mode and prep.dc is not None:
        try:
            file_metas_dump = (
                [m.model_dump() for m in prep.file_metas] if prep.file_metas else None
            )
            save_result(
                prep.dc.cache_key, prep.dc.canonical_json,
                llm_result, prep.meta, prep.dc.collection,
                database=database, ttl_seconds=prep.ttl_seconds,
                requested_at=prep.requested_at,
                file_metas=file_metas_dump,
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
        file_metas_dump = (
            [m.model_dump() for m in prep.file_metas] if prep.file_metas else None
        )
        save_result(
            prep.dc.cache_key, prep.dc.canonical_json,
            assembled, prep.meta, prep.dc.collection,
            database=database, ttl_seconds=prep.ttl_seconds,
            requested_at=prep.requested_at,
            file_metas=file_metas_dump,
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


# ── 파일 처리 (② Phase A 구현) ──────────────────────────────

def _apply_resolved(llm_input: dict, resolved: ResolvedFiles) -> dict:
    """ResolvedFiles를 llm_input에 녹여 call_input 완성.

    - inline_parts + uri_parts → contents 앞에 삽입
    - cached_content → 별도 필드로 세팅
    - file_checksums → 제거 (Gemini에 전달되면 안 되는 cache_key 계산용 필드)
    """
    call_input = dict(llm_input)

    contents = call_input.get("contents", "")
    if isinstance(contents, str):
        text_contents = [contents] if contents else []
    elif isinstance(contents, list):
        text_contents = [c for c in contents if isinstance(c, str)]
    else:
        text_contents = []

    call_input["contents"] = resolved.inline_parts + resolved.uri_parts + text_contents

    if resolved.cached_content:
        call_input["cached_content"] = resolved.cached_content

    call_input.pop("file_checksums", None)
    return call_input


async def _build_server_call_input(prep: _Prepared) -> dict:
    """파일 처리 후 call_input 구성.

    file_metas 없으면 llm_input 그대로. 있으면 resolve_files 호출하여
    inline/Files API 경로 처리 후 contents 조합.

    상세: docs/inline-vs-files-api-strategy.md §5, §7.2
    """
    if not prep.file_metas:
        return dict(prep.llm_input)

    resolved = await resolve_files(
        prep.file_metas,
        prep.llm_input,
        model=prep.llm_input.get("model"),
        system_instruction=prep.llm_input.get("system"),
        context_cache_ttl=prep.context_cache_ttl,
    )
    return _apply_resolved(prep.llm_input, resolved)


def _is_gemini_404(exc: Exception) -> bool:
    """Gemini 에러가 '리소스 없음(404)' 신호인지 추정.

    llm-handler가 정확한 예외 타입을 노출하지 않아 메시지 기반 판정 (보수적).
    llm-handler 담당자에게 예외 타입 세분화 요청 (docs/inline-vs-files-api-strategy.md §12 백로그).
    """
    msg = str(exc).lower()
    return "404" in msg or "not found" in msg


# ── Public API ─────────────────────────────────────────────────

async def llm_request_async(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (LLMResponse, meta) 튜플 반환.

    진입 직후 fail-fast 검증 (mime 화이트리스트 + 2GB 초과) → prepare → call → finalize.
    파일 처리 본문은 ② Phase A에서 구현.
    """
    if file_metas:
        validate_mime_types(file_metas)
        validate_file_sizes(file_metas)

    prep = prepare(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        return prep.cached_output

    call_input = await _build_server_call_input(prep)
    try:
        res = await aio_call(**call_input)
    except Exception as e:
        if _is_gemini_404(e) and prep.file_metas:
            logger.warning("Gemini 404 감지 — 자동 복구 시도: %s", e)
            resolved = await resolve_files_after_404(
                prep.file_metas, prep.llm_input,
                model=prep.llm_input.get("model"),
                system_instruction=prep.llm_input.get("system"),
                context_cache_ttl=prep.context_cache_ttl,
            )
            call_input = _apply_resolved(prep.llm_input, resolved)
            res = await aio_call(**call_input)  # 1회만 재시도 — 또 실패면 raise
        else:
            raise
    return finalize(prep, res, database=database)


def llm_request(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync)."""
    import asyncio
    return asyncio.run(llm_request_async(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> AsyncIterator[StreamChunk]:
    """스트리밍 LLM 요청 (async). StreamChunk를 yield.

    캐시 히트 시 단일 StreamChunk 1개 yield.
    스트림 완료 후 MDB에 결과 자동 저장.
    """
    if file_metas:
        validate_mime_types(file_metas)
        validate_file_sizes(file_metas)

    prep = prepare(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )

    if prep.cached_output:
        response, _meta = prep.cached_output
        yield _to_stream_chunk(response)
        return

    call_input = await _build_server_call_input(prep)

    accumulated: list[StreamChunk] = []
    try:
        async for chunk in aio_stream(**call_input):
            accumulated.append(chunk)
            yield chunk
    except Exception as e:
        # 404 복구는 아직 아무 chunk도 yield 안 된 상태에서만 의미 있음
        # (이미 일부 yield됐으면 부분 응답이 사용자에게 간 상태라 복구 시 중복)
        if _is_gemini_404(e) and prep.file_metas and not accumulated:
            logger.warning("Gemini 404 감지 — stream 자동 복구 시도: %s", e)
            resolved = await resolve_files_after_404(
                prep.file_metas, prep.llm_input,
                model=prep.llm_input.get("model"),
                system_instruction=prep.llm_input.get("system"),
                context_cache_ttl=prep.context_cache_ttl,
            )
            call_input = _apply_resolved(prep.llm_input, resolved)
            async for chunk in aio_stream(**call_input):
                accumulated.append(chunk)
                yield chunk
        else:
            raise
    _finalize_stream(prep, accumulated, database=database)


def llm_request_stream(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> Iterator[StreamChunk]:
    """스트리밍 LLM 요청 (sync)."""
    import asyncio

    async def _collect():
        results = []
        async for chunk in llm_request_stream_async(
            llm_input,
            file_metas=file_metas,
            cache_mode=cache_mode, save_mode=save_mode,
            ttl_seconds=ttl_seconds, debug=debug, database=database,
            context_cache_ttl=context_cache_ttl,
        ):
            results.append(chunk)
        return results

    return iter(asyncio.run(_collect()))
