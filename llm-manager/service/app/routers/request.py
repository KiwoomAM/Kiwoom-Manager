"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.core.errors import FileUploadError, ValidationError
from app.logic.done_check import done_check
from app.logic.file_handler import resolve_files
from app.logic.runner import llm_request_async, llm_request_stream_async
from app.logic.store import fetch_by_cache_key
from app.models.requests import DoneCheckBody, FetchBody, LLMRequestBody
from app.models.responses import (
    DoneCheckResponse,
    FetchResponse,
    FileUploadResponse,
    LLMRequestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── /v1/request ──────────────────────────────────────────────


@router.post("/v1/request", response_model=LLMRequestResponse)
async def handle_request(body: LLMRequestBody) -> LLMRequestResponse:
    """LLM 요청 (MDB 캐시 조회 → 호출 → 저장)."""
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    inline_files = None
    if body.inline_files:
        inline_files = [f.model_dump() for f in body.inline_files]

    llm_result, meta = await llm_request_async(
        body.llm_input,
        file_meta=file_meta,
        inline_files=inline_files,
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
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    inline_files = None
    if body.inline_files:
        inline_files = [f.model_dump() for f in body.inline_files]

    async def _sse():
        async for chunk in llm_request_stream_async(
            body.llm_input,
            file_meta=file_meta,
            inline_files=inline_files,
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


# ── /v1/files ────────────────────────────────────────────────


@router.post("/v1/files", response_model=FileUploadResponse)
async def handle_file_upload(
    files: list[UploadFile] = File(...),
    checksums: str = Form(...),
    context_cache_ttl: int | None = Form(None),
    model: str | None = Form(None),
    system_instruction: str | None = Form(None),
) -> FileUploadResponse:
    """파일 업로드 (Redis 파일 캐시 miss 시 SDK가 호출).

    multipart/form-data로 파일과 메타정보를 함께 수신.
    checksums: JSON 문자열 '[{"checksum":"sha256:...","filename":"...","size":123,"mime_type":"..."}]'
    """
    file_metas = json.loads(checksums)
    if len(files) != len(file_metas):
        raise ValidationError(
            f"파일 수({len(files)})와 checksum 수({len(file_metas)})가 일치하지 않습니다"
        )

    temp_paths: list[str] = []
    try:
        for upload_file, meta in zip(files, file_metas):
            suffix = f".{meta['filename'].rsplit('.', 1)[-1]}" if "." in meta["filename"] else ""
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            content = await upload_file.read()
            tmp.write(content)
            tmp.close()
            temp_paths.append(tmp.name)

        await resolve_files(
            file_metas,
            temp_paths,
            model=model,
            system_instruction=system_instruction,
            context_cache_ttl=context_cache_ttl,
        )
    except Exception as e:
        raise FileUploadError(f"파일 처리 실패: {e}") from e
    finally:
        for p in temp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    return FileUploadResponse(uploaded=[m["checksum"] for m in file_metas])


# ── /v1/done-check ───────────────────────────────────────────


@router.post("/v1/done-check", response_model=DoneCheckResponse)
async def handle_done_check(body: DoneCheckBody) -> DoneCheckResponse:
    """MDB 캐시(LLM 응답) 존재 여부 확인."""
    llm_input = body.llm_input
    if body.file_checksums:
        llm_input = {**llm_input, "file_checksums": body.file_checksums}

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
