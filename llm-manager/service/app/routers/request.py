"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.logic.done_check import done_check
from app.logic.keying import make_file_checksum
from app.logic.runner import llm_request_async, llm_request_stream_async
from app.logic.store import fetch_by_cache_key
from app.models.requests import DoneCheckBody, FetchBody, LLMRequestBody
from app.models.responses import (
    DoneCheckResponse,
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
        file_metas=body.file_metas,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        debug=body.debug,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
    )
    return LLMRequestResponse(result=llm_result.model_dump(), meta=meta)


# ── /v1/request/stream ──────────────────────────────────────


@router.post("/v1/request/stream")
async def handle_request_stream(body: LLMRequestBody) -> StreamingResponse:
    """스트리밍 LLM 요청 (SSE)."""
    async def _sse():
        async for chunk in llm_request_stream_async(
            body.llm_input,
            file_metas=body.file_metas,
            cache_mode=body.cache_mode,
            save_mode=body.save_mode,
            ttl_seconds=body.ttl_seconds,
            debug=body.debug,
            database=body.database,
            context_cache_ttl=body.context_cache_ttl,
        ):
            event = {
                "type": "chunk",
                "text": chunk.text,
                "finish_reason": chunk.finish_reason,
            }
            if chunk.usage:
                event["usage"] = chunk.usage.model_dump()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


# ── /v1/done-check ───────────────────────────────────────────


@router.post("/v1/done-check", response_model=DoneCheckResponse)
async def handle_done_check(body: DoneCheckBody) -> DoneCheckResponse:
    """MDB 캐시(LLM 응답) 존재 여부 확인."""
    llm_input = body.llm_input
    if body.file_metas:
        # FileMeta → checksum 리스트로 변환 후 cache_key 계산용으로 주입
        # (실제 LLM 호출엔 들어가지 않고 keying.canonicalize에서만 사용)
        checksums = [make_file_checksum(m) for m in body.file_metas]
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
    response, meta = fetch_by_cache_key(
        body.cache_key,
        database=body.database,
        as_stream=body.as_stream,
    )

    result_dict = response.model_dump() if hasattr(response, "model_dump") else {"text": "stream"}

    return FetchResponse(result=result_dict, meta=meta)
