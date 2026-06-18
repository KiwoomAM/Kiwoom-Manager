from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from llm_manager._internal.done_check import DoneCheckResult, done_check
from llm_manager._internal.file_processing import (
    ResolvedFiles,
    dedup,
    resolve_files,
    resolve_files_after_404,
)
from llm_manager._internal.gemini_errors import is_gemini_404
from llm_manager._internal.handler_client.aio import call as aio_call
from llm_manager._internal.handler_client.aio import last_assistant_message
from llm_manager._internal.handler_client.aio import stream as aio_stream
from llm_manager._internal.handler_client.models import Content, LLMResponse, StreamChunk
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.meta import build_meta
from llm_manager._internal.store import cache_get, save_result
from llm_manager._internal.stored_file_resolver import resolve_stored_files
from llm_manager.models import FileInput, StoredFile

logger = logging.getLogger(__name__)

# ── 내부 데이터 ────────────────────────────────────────────────

@dataclass
class _Prepared:
    llm_input: dict
    meta: dict
    cache_mode: bool
    save_mode: bool
    ttl_seconds: int | None
    requested_at: datetime
    dc: DoneCheckResult | None
    cached_output: tuple[LLMResponse, dict[str, Any]] | None
    route_reason: str | None
    files: list[FileInput] | None
    context_cache_ttl: int | None
    standby_ttl_seconds: int
    context_cache_id: str | None = None
    debug: bool = False


def _text_from_assistant_message(am: Content) -> str:
    """assistant_message 의 text part 들만 이어붙임 (cache HIT 시 text 노출용)."""
    if not isinstance(am.content, list):
        return am.content if isinstance(am.content, str) else ""
    return "".join(p.text for p in am.content if p.type == "text" and p.text)


# ── prepare / finalize ─────────────────────────────────────────

def prepare(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    database: str | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
    debug: bool = False,
) -> _Prepared:
    """캐시 조회 + 메타 준비. LLM 호출 전까지의 모든 로직.

    files의 hashing 3필드로 checksum을 자체 계산하여 cache_key 구성에 주입한다.
    """
    # service_name = 실행 서비스 식별자. env SERVICE_NAME 에서 주입한다
    # (메타 저장 + handler 전송 양쪽). 모든 서비스가 env 에 SERVICE_NAME 을 갖는다는 전제.
    # (구) llm_input 의 'service_name' 입력은 deprecated — 경고 후 env 값으로 덮어쓴다.
    if llm_input.get("service_name") is not None:
        logger.warning(
            "llm_input 의 'service_name' 입력은 deprecated 입니다 — 실행 서비스는 env "
            "SERVICE_NAME 로 자동 주입되며 입력 service_name 은 무시됩니다. "
            "호출 단위 라벨은 'task_name' 을 사용하세요."
        )
    llm_input = {**llm_input, "service_name": os.environ.get("SERVICE_NAME")}

    meta = build_meta(llm_input)
    requested_at = datetime.now(UTC)

    # ── files 처리 ─────────────────────────────────────
    if not (cache_mode or save_mode):
        files = None

    # keying용 입력: files가 있으면 checksum 리스트로 변환 후 주입
    if files:
        checksums = [make_file_checksum(fi) for fi in files]
        keying_input = {**llm_input, "file_checksums": checksums}
    else:
        keying_input = llm_input

    # ── ① cache_mode=F, save_mode=F → 캐시/키 불필요 ────────
    if not cache_mode and not save_mode:
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=False,
            ttl_seconds=ttl_seconds,
            requested_at=requested_at,
            dc=None, cached_output=None,
            route_reason="cache_off_save_off",
            files=None,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            debug=debug,
        )

    # ── ② cache_mode=F, save_mode=T → 키만 생성 ─────────────
    if not cache_mode:
        dc = done_check(keying_input, lookup_cache=False, database=database)
        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=False, save_mode=True,
            ttl_seconds=ttl_seconds,
            requested_at=requested_at,
            dc=dc, cached_output=None,
            route_reason="cache_off_save_on",
            files=files,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            debug=debug,
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
                ttl_seconds=ttl_seconds,
                requested_at=requested_at,
                dc=dc, cached_output=None,
                route_reason="cache_deserialize_error",
                files=files,
                context_cache_ttl=context_cache_ttl,
                standby_ttl_seconds=standby_ttl_seconds,
                debug=debug,
            )

        cached_meta: dict[str, Any] = {
            "source": "cache",
            "cache_hit": True,
            "cache_key": dc.cache_key,
            "metadata": dc.cached.get("metadata", {}),
            "collection": dc.collection,
        }
        file_metas = dc.cached.get("file_meta")
        if file_metas is not None:
            cached_meta["file_meta"] = file_metas
        if debug:
            cached_meta["route_reason"] = "cache_hit"

        return _Prepared(
            llm_input=llm_input, meta=meta,
            cache_mode=True, save_mode=save_mode,
            ttl_seconds=ttl_seconds,
            requested_at=requested_at,
            dc=dc, cached_output=(cached_result, cached_meta),
            route_reason="cache_hit",
            files=files,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            debug=debug,
        )

    # cache miss (또는 cache error)
    route_reason = "cache_error_fallback" if dc.cache_error else "cache_miss"
    return _Prepared(
        llm_input=llm_input, meta=meta,
        cache_mode=True, save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        requested_at=requested_at,
        dc=dc, cached_output=None,
        route_reason=route_reason,
        files=files,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
        debug=debug,
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
    file_metas = _dump_files_meta(prep.files)
    if file_metas is not None:
        meta["file_meta"] = file_metas
    if prep.context_cache_id is not None:
        meta["context_cache_id"] = prep.context_cache_id
    if prep.debug and prep.route_reason:
        meta["route_reason"] = prep.route_reason
    return meta


def _dump_files_meta(files: list[FileInput] | None) -> list[dict] | None:
    """MDB 저장용 meta dump — FileInput의 bytes는 제외."""
    if not files:
        return None
    return [fi.model_dump(exclude={"data"}) for fi in files]


async def _prepare_files(
    stored_files: list[StoredFile] | None,
) -> list[FileInput] | None:
    """Phase 3: stored_files → placeholder FileInput list. Method 1 폐기로 단일 인자.

    resolve_stored_files 가 validators 까지 처리. 실제 fetch 는 4·5단계에서 lazy.
    """
    if not stored_files:
        return None
    return await resolve_stored_files(stored_files)


def finalize(
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
                file_metas=_dump_files_meta(prep.files),
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
    prep: _Prepared,
    last: StreamChunk | None,
    *,
    database: str | None = None,
) -> None:
    """Stream 완료 후 handler done event 의 assistant_message 로 MDB 저장.

    handler 가 한 턴 전체를 done event 의 assistant_message 로 모아 보내므로,
    llm-manager 는 chunk 별 누적 없이 그대로 받아 저장한다 (PR #593 review §2).
    """
    if not prep.save_mode or prep.dc is None:
        return
    if last is None:
        logger.warning(
            "스트림 완료 시 청크가 0개: cache_key=%s — 빈 결과는 저장하지 않음",
            prep.dc.cache_key[:16] + "...",
        )
        return

    am = last_assistant_message()
    if am is None:
        logger.warning(
            "done event/assistant_message 미수신: cache_key=%s — 캐시 미저장",
            prep.dc.cache_key[:16] + "...",
        )
        return

    response = LLMResponse(
        text=_text_from_assistant_message(am),
        model=prep.llm_input.get("model", "unknown"),
        usage=last.usage,
        finish_reason=last.finish_reason,
        request_id="",  # raw stream 저장은 원래 빈 값 (helper 가 request_id 미노출)
        assistant_message=am,
    )
    finalize(prep, response, database=database)   # 기존 저장 로직 재사용


def _to_stream_chunk(response: LLMResponse) -> StreamChunk:
    """LLMResponse를 StreamChunk 1개로 변환한다 (캐시 히트 시 사용)."""
    return StreamChunk(
        text=response.text,
        finish_reason=response.finish_reason,
        usage=response.usage,
        function_calls=response.function_calls,
        tool_calls=response.tool_calls,
        tool_responses=response.tool_responses,
        code_blocks=response.code_blocks,
        code_results=response.code_results,
        grounding=response.grounding,
    )


# ── 파일 처리 ────────────────────────────────────────────────

def _apply_resolved(llm_input: dict, resolved: ResolvedFiles) -> dict:
    """ResolvedFiles를 llm_input에 녹여 call_input 완성.

    - inline_parts → contents 앞에 삽입 (context cache 와 무관, 항상 유지)
    - uri_parts → cached_content 없을 때만 contents 에 삽입.
                  cached_content 있으면 같은 file_refs 가 cache 안에 이미 포함되어
                  있어 contents 에 또 넣으면 토큰 중복 + Gemini Bad Request.
    - cached_content → 있으면 별도 필드로 세팅 + system/tools/tool_config 제거
                        (이것들은 cache 생성 시 이미 포함됨, request 에 또 넣으면 충돌)
    - file_checksums → 제거 (Gemini에 전달되면 안 되는 cache_key 계산용 필드)

    근거: packages/llm-handler/CLAUDE.md Gotchas — "cached_content 사용 시
    tools/system/tool_config 동시 전달 불가 (Gemini API 제약). 캐시에 설정했으면
    call()에서 재지정하면 Bad Request"
    """
    call_input = dict(llm_input)

    contents = call_input.get("contents", "")
    if isinstance(contents, str):
        text_contents = [contents] if contents else []
    elif isinstance(contents, list):
        text_contents = [c for c in contents if isinstance(c, str)]
    else:
        # dict / None / int / bytes 등은 silent drop 하지 않고 fail-fast.
        # caller 의 입력 오류(오타 등)를 조용히 덮지 않도록 명시적 에러.
        raise TypeError(
            f"llm_input['contents'] 는 str 또는 list 여야 합니다: "
            f"실제 {type(contents).__name__}"
        )

    if resolved.cached_content:
        call_input["cached_content"] = resolved.cached_content
        call_input["contents"] = resolved.inline_parts + text_contents
        call_input.pop("system", None)
        call_input.pop("tools", None)
        call_input.pop("tool_config", None)
    else:
        call_input["contents"] = resolved.inline_parts + resolved.uri_parts + text_contents

    call_input.pop("file_checksums", None)
    return call_input


async def _build_server_call_input(prep: _Prepared) -> dict:
    """파일 처리 후 call_input 구성.

    files 없으면 llm_input 그대로. 있으면 resolve_files 호출하여
    inline/Files API 경로 처리 후 contents 조합.

    상세: docs/07.inline-vs-files-api-strategy.md §5, §7.2
    """
    if not prep.files:
        return dict(prep.llm_input)

    resolved = await resolve_files(
        prep.files,
        prep.llm_input,
        model=prep.llm_input.get("model"),
        system_instruction=prep.llm_input.get("system"),
        context_cache_ttl=prep.context_cache_ttl,
        standby_ttl_seconds=prep.standby_ttl_seconds,
        tools=prep.llm_input.get("tools"),
        tool_config=prep.llm_input.get("tool_config"),
    )
    prep.context_cache_id = resolved.context_cache_id
    return _apply_resolved(prep.llm_input, resolved)


# ── agent_llm standby 헬퍼 (08.dedup-design-and-impl §6 모듈 구조) ────────


def _recheck_agent_llm_cache(
    prep: _Prepared, database: str | None,
) -> tuple[LLMResponse, dict[str, Any]] | None:
    """MDB를 직접 조회하여 HIT면 (response, meta), MISS/역직렬화 실패면 None.

    standby 대기 중 주기적으로 호출 — 다른 요청이 LLM을 끝내고 MDB에
    저장했는지 확인.
    """
    if not prep.dc:
        return None
    doc = cache_get(prep.dc.cache_key, prep.dc.collection, database=database)
    if doc is None:
        return None
    try:
        response = LLMResponse.model_validate(doc.get("result", {}))
    except Exception:
        logger.warning(
            "standby 대기 중 캐시 역직렬화 실패: cache_key=%s",
            prep.dc.cache_key[:16],
        )
        return None
    meta: dict[str, Any] = {
        "source": "cache",
        "cache_hit": True,
        "cache_key": prep.dc.cache_key,
        "metadata": doc.get("metadata", {}),
        "collection": prep.dc.collection,
    }
    if prep.debug:
        meta["route_reason"] = "standby_wait_hit"
    return response, meta


async def _do_llm_call(
    prep: _Prepared, database: str | None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """파일 처리 + LLM 호출 + 404 자동 복구 + finalize 까지 실행."""
    call_input = await _build_server_call_input(prep)
    try:
        res = await aio_call(**call_input)
    except Exception as e:
        if is_gemini_404(e) and prep.files:
            logger.warning("Gemini 404 감지 — 자동 복구 시도: %s", e)
            resolved = await resolve_files_after_404(
                prep.files, prep.llm_input,
                model=prep.llm_input.get("model"),
                system_instruction=prep.llm_input.get("system"),
                context_cache_ttl=prep.context_cache_ttl,
                standby_ttl_seconds=prep.standby_ttl_seconds,
                tools=prep.llm_input.get("tools"),
                tool_config=prep.llm_input.get("tool_config"),
            )
            prep.context_cache_id = resolved.context_cache_id
            call_input = _apply_resolved(prep.llm_input, resolved)
            res = await aio_call(**call_input)  # 1회만 재시도 — 또 실패면 raise
        else:
            raise
    return finalize(prep, res, database=database)


# ── Public API ─────────────────────────────────────────────────

async def llm_request_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    database: str | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
    debug: bool = False,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). Phase 3: stored_files 만 받음 (Method 1 폐기)."""
    if not (cache_mode or save_mode):
        stored_files = None

    combined = await _prepare_files(stored_files)

    prep = prepare(
        llm_input,
        files=combined,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
        debug=debug,
    )
    if prep.cached_output:
        return prep.cached_output

    # dc 없는 경우(cache_off_save_off) → 공유 캐시 없어서 standby 불필요
    if prep.dc is None:
        return await _do_llm_call(prep, database)

    # cache miss — agent_llm standby 선점 시도
    cache_key = prep.dc.cache_key
    acquired = await dedup.try_acquire_standby(
        "agent_llm", cache_key, standby_ttl_seconds,
    )
    if acquired:
        logger.info("[dedup] role=leader key=%s", cache_key[:16])
        try:
            return await _do_llm_call(prep, database)
        finally:
            await dedup.release_standby("agent_llm", cache_key)

    # 다른 요청이 처리 중 → 대기
    t_wait = time.monotonic()
    cached = await dedup.wait_for_standby_release(
        "agent_llm", cache_key, standby_ttl_seconds,
        find_cache_fn=lambda: _recheck_agent_llm_cache(prep, database),
    )
    wait_ms = int((time.monotonic() - t_wait) * 1000)
    if cached:
        logger.info("[dedup] role=waiter wait_ms=%d leader_completed=true", wait_ms)
        return cached
    # timeout → 자기가 LLM 호출 (Y' Hybrid)
    logger.warning("[dedup] role=standby_timeout wait_ms=%d", wait_ms)
    return await _do_llm_call(prep, database)


def llm_request(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    database: str | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
    debug: bool = False,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync)."""
    import asyncio
    return asyncio.run(llm_request_async(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
        debug=debug,
    ))


async def _do_llm_stream(
    prep: _Prepared, database: str | None,
) -> AsyncIterator[StreamChunk]:
    """파일 처리 + LLM stream + 404 자동 복구 + stream finalize."""
    call_input = await _build_server_call_input(prep)
    # chunk 는 그대로 흘리고, 저장은 handler done event 의 assistant_message 로 (PR #593 review §2)
    last: StreamChunk | None = None
    try:
        async for chunk in aio_stream(**call_input):
            last = chunk
            yield chunk
    except Exception as e:
        # 404 복구는 아직 아무 chunk도 yield 안 된 상태에서만 의미 있음
        if is_gemini_404(e) and prep.files and last is None:
            logger.warning("Gemini 404 감지 — stream 자동 복구 시도: %s", e)
            resolved = await resolve_files_after_404(
                prep.files, prep.llm_input,
                model=prep.llm_input.get("model"),
                system_instruction=prep.llm_input.get("system"),
                context_cache_ttl=prep.context_cache_ttl,
                standby_ttl_seconds=prep.standby_ttl_seconds,
                tools=prep.llm_input.get("tools"),
                tool_config=prep.llm_input.get("tool_config"),
            )
            prep.context_cache_id = resolved.context_cache_id
            call_input = _apply_resolved(prep.llm_input, resolved)
            async for chunk in aio_stream(**call_input):
                last = chunk
                yield chunk
        else:
            # 404 외 예외 (rate limit / 네트워크 / etc) — partial chunk 는 이미 yield 됐으나
            # 불완전 응답을 캐시에 남기지 않기 위해 finalize 호출 안 함. 동일 요청 재호출 시 LLM 재과금 발생.
            logger.warning("스트림 중 비-404 예외 — partial 응답 폐기, 캐시 미저장: %s", e)
            raise
    _finalize_stream(prep, last, database=database)


async def llm_request_stream_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    database: str | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
    debug: bool = False,
) -> tuple[AsyncIterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (async). Phase 3: stored_files 만 받음."""
    if not (cache_mode or save_mode):
        stored_files = None

    combined = await _prepare_files(stored_files)

    prep = prepare(
        llm_input,
        files=combined,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
        debug=debug,
    )

    if prep.cached_output:
        response, cached_meta = prep.cached_output

        async def _cached_stream() -> AsyncIterator[StreamChunk]:
            yield _to_stream_chunk(response)

        return _cached_stream(), cached_meta

    # dc 없는 경우 → 공유 캐시 없어서 standby 불필요
    if prep.dc is None:
        return _do_llm_stream(prep, database), _build_live_meta(prep)

    # cache miss — agent_llm standby 선점 시도
    cache_key = prep.dc.cache_key
    acquired = await dedup.try_acquire_standby(
        "agent_llm", cache_key, standby_ttl_seconds,
    )
    if acquired:
        logger.info("[dedup] role=leader key=%s", cache_key[:16])
        meta = _build_live_meta(prep)

        async def _leader_stream() -> AsyncIterator[StreamChunk]:
            try:
                async for chunk in _do_llm_stream(prep, database):
                    yield chunk
            finally:
                await dedup.release_standby("agent_llm", cache_key)

        return _leader_stream(), meta

    # 다른 요청이 처리 중 → 대기
    t_wait = time.monotonic()
    cached = await dedup.wait_for_standby_release(
        "agent_llm", cache_key, standby_ttl_seconds,
        find_cache_fn=lambda: _recheck_agent_llm_cache(prep, database),
    )
    wait_ms = int((time.monotonic() - t_wait) * 1000)
    if cached:
        logger.info("[dedup] role=waiter wait_ms=%d leader_completed=true", wait_ms)
        response, cached_meta = cached

        async def _cached_stream() -> AsyncIterator[StreamChunk]:
            yield _to_stream_chunk(response)

        return _cached_stream(), cached_meta

    # timeout → 자기가 stream (Y' Hybrid)
    logger.warning("[dedup] role=standby_timeout wait_ms=%d", wait_ms)
    return _do_llm_stream(prep, database), _build_live_meta(prep)


def llm_request_stream(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    database: str | None = None,
    context_cache_ttl: int | None = None,
    standby_ttl_seconds: int = 600,
    debug: bool = False,
) -> tuple[Iterator[StreamChunk], dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). Phase 3: stored_files 만 받음."""
    import asyncio

    async def _collect() -> tuple[list[StreamChunk], dict[str, Any]]:
        stream, meta = await llm_request_stream_async(
            llm_input,
            stored_files=stored_files,
            cache_mode=cache_mode, save_mode=save_mode,
            ttl_seconds=ttl_seconds, database=database,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
            debug=debug,
        )
        chunks: list[StreamChunk] = []
        async for chunk in stream:
            chunks.append(chunk)
        return chunks, meta

    chunks, meta = asyncio.run(_collect())
    return iter(chunks), meta
