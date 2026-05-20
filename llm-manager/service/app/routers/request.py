"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from llm_handler.exceptions import HandlerConnectionError, HandlerError

from app.core.errors import CacheKeyNotFoundError
from app.logic.context_cache_lifecycle import expire_caches
from app.logic.done_check import done_check
from app.logic.keying import make_file_checksum
from app.logic.runner import llm_request_async, llm_request_stream_async
from app.logic.store import fetch_by_cache_key
from app.models.requests import (
    DoneCheckBody,
    ExpireContextCacheRequest,
    FetchBody,
    LLMRequestBody,
)
from app.models.responses import (
    DoneCheckResponse,
    ExpireContextCacheResponse,
    FetchResponse,
    LLMRequestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── /v1/request ──────────────────────────────────────────────


@router.post("/v1/request", response_model=LLMRequestResponse)
async def handle_request(body: LLMRequestBody) -> LLMRequestResponse:
    """LLM 요청 (MDB 캐시 조회 → 호출 → 저장)."""
    llm_result, meta = await llm_request_async(
        body.llm_input,
        stored_files=body.stored_files,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
        standby_ttl_seconds=body.standby_ttl_seconds,
        debug=body.debug,
    )
    return LLMRequestResponse(result=llm_result.model_dump(), meta=meta)


# ── /v1/request/stream ──────────────────────────────────────


@router.post("/v1/request/stream")
async def handle_request_stream(body: LLMRequestBody) -> StreamingResponse:
    """스트리밍 LLM 요청 (SSE).

    meta는 `X-LLM-Meta` 응답 헤더에 URL-encoded JSON 으로 담고,
    body는 chunk 이벤트만 SSE로 내보낸다.
    """
    stream, meta = await llm_request_stream_async(
        body.llm_input,
        stored_files=body.stored_files,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
        standby_ttl_seconds=body.standby_ttl_seconds,
        debug=body.debug,
    )

    async def _sse():
        # mid-stream 에러는 응답 본문이 이미 시작돼 HTTP status 로 못 보낸다.
        # llm-handler 와 동일하게 "error" 키 SSE 이벤트로 in-band relay 후 [DONE] 생략.
        try:
            async for chunk in stream:
                event = {
                    "type": "chunk",
                    "text": chunk.text,
                    "finish_reason": chunk.finish_reason,
                }
                if chunk.usage:
                    event["usage"] = chunk.usage.model_dump()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except HandlerError as e:
            # llm-handler 분류 에러 — status_code/error_type/detail 그대로 relay.
            err = {
                "error": e.message,
                "error_type": e.error_type,
                "status_code": e.status_code,
                "detail": e.detail,
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return
        except HandlerConnectionError as e:
            # llm-handler 도달 불가 → 502 handler.
            err = {
                "error": e.message,
                "error_type": "handler",
                "status_code": 502,
                "detail": None,
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return
        except Exception as e:
            # llm-request 자체 미처리 예외 — 비스트림 catch-all 과 동일하게
            # handler_internal + detail=예외클래스명.
            logger.exception("스트림 미처리 예외: %s", type(e).__name__)
            err = {
                "error": "내부 서버 오류",
                "error_type": "handler_internal",
                "status_code": 500,
                "detail": type(e).__name__,
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return
        yield "data: [DONE]\n\n"

    headers = {
        "X-LLM-Meta": quote(json.dumps(meta, default=str, ensure_ascii=False)),
    }
    return StreamingResponse(_sse(), media_type="text/event-stream", headers=headers)


# ── /v1/done-check ───────────────────────────────────────────


@router.post("/v1/done-check", response_model=DoneCheckResponse)
async def handle_done_check(body: DoneCheckBody) -> DoneCheckResponse:
    """MDB 캐시(LLM 응답) 존재 여부 확인."""
    llm_input = body.llm_input
    if body.files:
        # FileKeyingMeta → checksum 리스트로 변환 후 cache_key 계산용으로 주입
        # (실제 LLM 호출엔 들어가지 않고 keying.canonicalize에서만 사용)
        checksums = [make_file_checksum(m) for m in body.files]
        llm_input = {**llm_input, "file_checksums": checksums}

    result = done_check(llm_input, lookup_cache=True, database=body.database)

    return DoneCheckResponse(
        cache_key=result.cache_key,
        canonical_json=result.canonical_json,
        cached=result.cached,
        cache_hit=result.cached is not None,
        collection=result.collection,
    )


# ── /v1/fetch ────────────────────────────────────────────────


@router.post("/v1/fetch", response_model=FetchResponse)
async def handle_fetch(body: FetchBody) -> FetchResponse:
    """캐시 키로 결과 직접 조회."""
    try:
        response, meta = fetch_by_cache_key(
            body.cache_key,
            database=body.database,
        )
    except KeyError as e:
        # cache_key 문서 없음 — 불투명한 500 대신 404 not_found 로 명확히 신호.
        raise CacheKeyNotFoundError(str(e)) from e
    return FetchResponse(result=response.model_dump(), meta=meta)


# ── /v1/context-cache/expire ─────────────────────────────────


@router.post(
    "/v1/context-cache/expire",
    response_model=ExpireContextCacheResponse,
)
async def handle_expire_context_cache(
    body: ExpireContextCacheRequest,
) -> ExpireContextCacheResponse:
    """Context Cache TTL 단축 — Gemini caches.update + Redis EXPIRE.

    Cache 청구는 새 TTL 기준으로 prorate 처리됨. design: docs/20.context-cache-expire.md
    """
    return await expire_caches(body.cache_ids, body.grace_seconds)
