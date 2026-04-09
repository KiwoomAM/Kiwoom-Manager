"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, UploadFile

from app.core.errors import FileUploadError, ValidationError
from app.logic.done_check import done_check
from app.logic.file_handler import resolve_files
from app.logic.file_handler.cache_store import (
    find_context_cache,
    find_files_api,
    make_context_cache_key,
)
from app.logic.runner import _build_call_input, _finalize, _prepare
from app.logic.store import fetch_by_cache_key
from app.models.requests import DoneCheckBody, FetchBody, LLMRequestBody
from app.models.responses import (
    DoneCheckResponse,
    FetchResponse,
    FileUploadResponse,
    LLMRequestResponse,
)

from llm_handler.aio import call as aio_call
from llm_handler.models import Part

logger = logging.getLogger(__name__)

router = APIRouter()


# ── /v1/request ──────────────────────────────────────────────


@router.post("/v1/request", response_model=LLMRequestResponse)
async def handle_request(body: LLMRequestBody) -> LLMRequestResponse:
    """LLM 요청 (MDB 캐시 조회 → 호출 → 저장).

    SDK에서 file_checksums를 전달하면 서버가 캐시 키에 반영한다.
    - inline_files: ≤20MB 파일은 base64로 직접 포함 (Redis/Files API 불필요)
    - file_checksums만: >20MB 파일은 /v1/files로 사전 업로드 필요
    """
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    prep = _prepare(
        body.llm_input,
        file_meta=file_meta,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        debug=body.debug,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
    )

    # MDB 캐시 히트
    if prep.cached_output:
        response, meta = prep.cached_output
        return LLMRequestResponse(result=response.model_dump(), meta=meta)

    # MDB 캐시 미스 → LLM 호출
    call_input = dict(prep.llm_input)

    # ── inline 파일 처리 (≤20MB, base64 → Part) ──
    inline_parts: list = []
    inline_checksums: set[str] = set()
    if body.inline_files:
        import base64
        for f in body.inline_files:
            raw = base64.b64decode(f.data_base64)
            inline_parts.append(Part.from_bytes(data=raw, mime_type=f.mime_type))
            inline_checksums.add(f.checksum)

    # ── >20MB 파일: Redis 파일 캐시에서 URI 조회 ──
    if prep.file_meta:
        large_file_meta = [m for m in prep.file_meta if m["checksum"] not in inline_checksums]
        if large_file_meta:
            call_input = _build_call_input_from_cache(
                call_input, large_file_meta, prep.context_cache_ttl,
            )

    # ── inline parts를 contents에 추가 ──
    if inline_parts:
        contents = call_input.get("contents", "")
        if isinstance(contents, str):
            contents = [contents] if contents else []
        elif isinstance(contents, list):
            contents = [c for c in contents if isinstance(c, str)]
        call_input["contents"] = inline_parts + contents
        call_input.pop("file_checksums", None)

    res = await aio_call(**call_input)
    llm_result, meta = _finalize(prep, res, database=body.database)

    return LLMRequestResponse(result=llm_result.model_dump(), meta=meta)


def _build_call_input_from_cache(
    call_input: dict,
    file_meta: list[dict],
    context_cache_ttl: int | None,
) -> dict:
    """Redis 파일 캐시에서 URI를 조회하여 call_input을 구성한다."""
    checksums = [m["checksum"] for m in file_meta]

    # context cache 확인
    hash_key = make_context_cache_key(
        checksums,
        model=call_input.get("model"),
        system_instruction=call_input.get("system"),
    )
    cached = find_context_cache(hash_key)
    if cached:
        call_input["cached_content"] = cached["cached_content_name"]
        contents = call_input.get("contents")
        if isinstance(contents, list):
            call_input["contents"] = [c for c in contents if isinstance(c, str)]
        call_input.pop("file_checksums", None)
        return call_input

    # 개별 파일 URI 조회
    contents_parts: list = []
    for meta in file_meta:
        existing = find_files_api(meta["checksum"])
        if existing:
            if existing.get("split"):
                for uri in existing["uris"]:
                    contents_parts.append(Part.from_uri(uri, mime_type=meta["mime_type"]))
            else:
                contents_parts.append(
                    Part.from_uri(existing["uris"][0], mime_type=meta["mime_type"])
                )
        else:
            raise FileUploadError(
                f"파일 URI를 찾을 수 없습니다: {meta['checksum'][:16]}... "
                "— /v1/files로 먼저 업로드하세요"
            )

    # contents에 Part 추가 (파일 먼저, 텍스트 뒤)
    contents = call_input.get("contents", "")
    if isinstance(contents, str):
        contents = [contents] if contents else []
    elif isinstance(contents, list):
        contents = [c for c in contents if isinstance(c, str)]
    call_input["contents"] = contents_parts + contents
    call_input.pop("file_checksums", None)

    return call_input


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
