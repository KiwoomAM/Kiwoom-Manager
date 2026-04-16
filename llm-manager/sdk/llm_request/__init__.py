"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from file_manager import FileMeta, resolve   # file_manager SDK 사용
    from llm_request import llm_request_async

    file_metas = [resolve("report.pdf")]   # FileMeta 리스트
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        file_metas=file_metas,
    )
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import llm_handler as llm_handler  # noqa: PLC0414  (방법 2: 모듈 접근 — llm_handler.aio.call 등)
import llm_handler.aio  # noqa: F401  (llm_handler.aio 속성 보장)
from llm_handler import (
    CacheRef,
    ContentsType,
    EmbeddingStatistics,
    EmbeddingValue,
    EmbedResponse,
    FileRef,
    FunctionCall,
    HandlerConnectionError,
    HandlerError,
    LLMResponse,
    Part,
    Session,
    StreamChunk,
    TokenUsage,
    call,
    configure,
    create_cache,
    delete_cache,
    delete_file,
    embed,
    get_cache,
    get_file,
    list_caches,
    list_files,
    session,
    stream,
    update_cache,
    upload_file,
)

from llm_request import aio as aio  # noqa: PLC0414  (llm_request.aio 서브모듈 노출)
from llm_request._client import AsyncClient
from file_manager import FileMeta
from llm_request.exceptions import SDKError, ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, RequestResult

__all__ = [
    # llm_request 자체 API
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "done_check",
    "fetch_by_cache_key",
    "FileMeta",
    "SDKError",
    "ServiceConnectionError",
    "ServiceError",
    # llm_handler 모듈 (방법 2 — llm_handler.configure, llm_handler.SDKError 등)
    "llm_handler",
    # llm_request.aio 서브모듈 (llm_handler.aio re-export)
    "aio",
    # llm_handler flat re-export (방법 1 — sync)
    "call",
    "stream",
    "embed",
    "session",
    "Session",
    "configure",
    "upload_file",
    "create_cache",
    "delete_cache",
    "list_caches",
    "CacheRef",
    "ContentsType",
    "EmbeddingStatistics",
    "EmbeddingValue",
    "EmbedResponse",
    "FileRef",
    "FunctionCall",
    "LLMResponse",
    "Part",
    "StreamChunk",
    "TokenUsage",
    "HandlerConnectionError",
    "HandlerError",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    """서비스 URL을 환경변수에서 읽기."""
    return os.getenv("LLM_REQUEST_URL", "http://localhost:8101")


def _get_client() -> AsyncClient:
    """싱글톤 AsyncClient 반환."""
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


def _configure(base_url: str | None = None, timeout: float = 120.0) -> None:
    """llm-request 서비스 URL 내부 설정 (외부 미공개).

    외부는 LLM_REQUEST_URL 환경변수로 설정. 테스트/동적 오버라이드용.
    """
    global _instance
    url = base_url or _resolve_base_url()
    _instance = AsyncClient(base_url=url, timeout=timeout)


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
    standby_ttl_seconds: int = 600,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (result, meta) 튜플 반환.

    file_metas는 file_manager가 제공하는 metadata 리스트.
    SDK는 파일 bytes를 보내지 않는다 (file_manager 분리 설계).

    Args:
        standby_ttl_seconds: cache stampede dedup standby TTL + 대기 timeout (기본 600초).
    """
    client = _get_client()
    result = await client.request(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
    )
    return result["result"], result["meta"]


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
    standby_ttl_seconds: int = 600,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). llm_request_async의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
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
    standby_ttl_seconds: int = 600,
) -> AsyncIterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (async). SSE 이벤트를 파싱하여 dict로 yield."""
    client = _get_client()
    async for event in client.request_stream(
        llm_input,
        file_metas=file_metas,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
        standby_ttl_seconds=standby_ttl_seconds,
    ):
        yield event


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
    standby_ttl_seconds: int = 600,
) -> Iterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). llm_request_stream_async의 동기 래퍼."""
    async def _collect():
        results = []
        async for event in llm_request_stream_async(
            llm_input,
            file_metas=file_metas,
            cache_mode=cache_mode,
            save_mode=save_mode,
            ttl_seconds=ttl_seconds,
            debug=debug,
            database=database,
            context_cache_ttl=context_cache_ttl,
            standby_ttl_seconds=standby_ttl_seconds,
        ):
            results.append(event)
        return results

    return iter(asyncio.run(_collect()))


async def done_check(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시(LLM 응답) 존재 여부 확인."""
    client = _get_client()
    return await client.done_check(
        llm_input,
        file_metas=file_metas,
        database=database,
    )


async def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """캐시 키로 결과 직접 조회."""
    client = _get_client()
    result = await client.fetch(cache_key, database=database)
    return result["result"], result["meta"]
