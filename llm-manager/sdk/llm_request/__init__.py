"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from pathlib import Path
    import mimetypes
    from llm_request import llm_request_async, FileInput

    p = Path("report.pdf")
    file_input = FileInput(
        file_name=p.name,
        file_size=p.stat().st_size,
        file_mtime=int(p.stat().st_mtime),
        mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        extension=p.suffix.lstrip("."),
        data=p.read_bytes(),
    )
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        files=[file_input],
    )
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any, TypedDict

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
from llm_request.exceptions import SDKError, ServiceConnectionError, ServiceError
from llm_request.models import (
    DoneCheckResult,
    FileInput,
    FileKeyingMeta,
    RequestMeta,
    RequestResult,
)

__all__ = [
    # llm_request 자체 API
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "done_check",
    "fetch_by_cache_key",
    "Timings",
    "FileInput",
    "FileKeyingMeta",
    "RequestMeta",
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
    "delete_file",
    "get_file",
    "list_files",
    "create_cache",
    "delete_cache",
    "get_cache",
    "list_caches",
    "update_cache",
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


# HTTP 클라이언트를 어디에, 어떻게 만드나 설정
_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    """서비스 URL을 환경변수에서 읽기."""
    return os.getenv("LLM_REQUEST_URL", "http://192.7.9.45:8101")


def _get_client() -> AsyncClient:
    """싱글톤 AsyncClient 반환."""
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


# ── Timings ───────────────────────────────────────────────────


class Timings(TypedDict, total=False):
    """요청 하나의 시간 관련 설정 묶음.

    모든 키는 선택 — 생략 시 `_DEFAULT_TIMINGS` 적용.
    """

    http_timeout: float
    """SDK → llm-request 서비스 HTTP 응답 대기 시간 (초). 기본 600."""

    mdb_ttl: int | None
    """MDB 응답 캐시 수명 (초). None = 무기한. 기본 None."""

    context_cache_ttl: int | None
    """Gemini Context Cache 수명 (초). None = 생성 안 함 (opt-in). 기본 None."""

    standby_ttl: int
    """Cache stampede dedup standby TTL + waiter 대기 timeout (초). 기본 600."""


_DEFAULT_TIMINGS: Timings = {
    "http_timeout": 600.0,
    "mdb_ttl": None,
    "context_cache_ttl": None,
    "standby_ttl": 600,
}


def _resolve_timings(timings: Timings | None) -> Timings:
    """사용자 timings와 기본값 병합."""
    if not timings:
        return dict(_DEFAULT_TIMINGS)  # type: ignore[return-value]
    return {**_DEFAULT_TIMINGS, **timings}  # type: ignore[return-value]


# ── Public API ─────────────────────────────────────────────────

async def llm_request_async(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
) -> tuple[LLMResponse, RequestMeta]:
    """단일 LLM 요청 실행 (async). (result, meta) 튜플 반환.

    files: 에이전트가 구성한 FileInput 리스트 (meta + bytes).
    서버가 내부에서 file-manager에 등록 후 처리한다.

    Args:
        timings: 시간 관련 설정 묶음. 일부 키만 지정하면 나머지는 기본값 적용.
                 예) timings={"http_timeout": 1200, "context_cache_ttl": 3600}
    """
    t = _resolve_timings(timings)
    client = _get_client()
    result = await client.request(
        llm_input,
        files=files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        http_timeout=t["http_timeout"],
    )
    return LLMResponse(**result["result"]), RequestMeta(**result["meta"])


def llm_request(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
) -> tuple[LLMResponse, RequestMeta]:
    """단일 LLM 요청 실행 (sync). llm_request_async의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        files=files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        database=database,
        timings=timings,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (async). SSE 이벤트를 파싱하여 dict로 yield."""
    t = _resolve_timings(timings)
    client = _get_client()
    async for event in client.request_stream(
        llm_input,
        files=files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        http_timeout=t["http_timeout"],
    ):
        yield event


def llm_request_stream(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
) -> Iterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). llm_request_stream_async의 동기 래퍼."""
    async def _collect():
        results = []
        async for event in llm_request_stream_async(
            llm_input,
            files=files,
            cache_mode=cache_mode,
            save_mode=save_mode,
            database=database,
            timings=timings,
        ):
            results.append(event)
        return results

    return iter(asyncio.run(_collect()))


async def done_check(
    llm_input: dict,
    *,
    files: list[FileKeyingMeta] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시(LLM 응답) 존재 여부 확인.

    files: FileKeyingMeta 리스트 (hashing 3필드만). bytes 불필요.
    """
    client = _get_client()
    return await client.done_check(
        llm_input,
        files=files,
        database=database,
    )


async def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[LLMResponse, RequestMeta]:
    """캐시 키로 결과 직접 조회."""
    client = _get_client()
    result = await client.fetch(cache_key, database=database)
    return LLMResponse(**result["result"]), RequestMeta(**result["meta"])
