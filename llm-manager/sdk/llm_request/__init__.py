"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from pathlib import Path
    from file_manager import FileManagerHandler
    from llm_request import llm_request_async, StoredFile

    fm = FileManagerHandler()
    p = Path("report.pdf")
    sf_dict = await fm.register_or_get(
        bytes_data=p.read_bytes(),
        metadata=fm.build_metadata(
            file_name=p.name,
            file_mtime=int(p.stat().st_mtime),
            file_size=p.stat().st_size,
        ),
    )
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        stored_files=[StoredFile(**sf_dict)],
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
    ExpireFailure,
    ExpireResult,
    FileInput,
    FileKeyingMeta,
    RequestMeta,
    RequestResult,
    StoredFile,
)

__all__ = [
    # llm_request 자체 API
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "done_check",
    "fetch_by_cache_key",
    "delete_cache",
    "delete_cache_async",
    "close",
    "Timings",
    "FileInput",
    "FileKeyingMeta",
    "StoredFile",
    "RequestMeta",
    "ExpireResult",
    "ExpireFailure",
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
_instance_lock = asyncio.Lock()  # Python 3.10+ 에선 이벤트 루프 없이도 생성 가능


def _resolve_base_url() -> str:
    """서비스 URL을 환경변수에서 읽기."""
    return os.getenv("LLM_REQUEST_URL", "http://192.7.9.45:8101")


async def _get_client() -> AsyncClient:
    """싱글톤 AsyncClient 반환 (double-checked locking).

    check-then-act race 방지 — 동시 첫 호출에서 AsyncClient 중복 생성 방지.
    상세: docs/00.concepts.md §"Race Condition, Lock, Single-flight / Standby"
    """
    global _instance
    if _instance is None:                       # 빠른 경로
        async with _instance_lock:
            if _instance is None:               # lock 획득 후 재확인
                _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


async def close() -> None:
    """SDK 가 사용 중인 AsyncClient 를 명시적으로 정리한다.

    agent 프로세스 종료 전에 호출하여 httpx 커넥션 풀·소켓을 graceful 하게 반납.
    호출하지 않아도 프로세스 종료 시 OS 가 정리하지만 `ResourceWarning: unclosed`
    경고가 로그에 남을 수 있다.

    사용 예:
        try:
            response, meta = await llm_request_async(...)
        finally:
            await close()
    """
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


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
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[LLMResponse, RequestMeta]:
    """단일 LLM 요청 실행 (async). Phase 3: stored_files 만 받음 (Method 1 폐기).

    stored_files: 이미 file-manager 에 등록된 파일 참조 (StoredFile, 6 필드).
    agent 가 file-manager SDK 의 `register_or_get` 으로 등록 후 받은 dict 를
    StoredFile 로 변환해 전달. 서버가 file-manager 에서 bytes 만 fetch.

    Args:
        timings: 시간 관련 설정 묶음. 일부 키만 지정하면 나머지는 기본값 적용.
                 예) timings={"http_timeout": 1200, "context_cache_ttl": 3600}
        debug: True면 meta.route_reason에 요청 처리 경로가 포함된다 (기본 False).
    """
    t = _resolve_timings(timings)
    client = await _get_client()
    result = await client.request(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        http_timeout=t["http_timeout"],
        debug=debug,
    )
    return LLMResponse(**result["result"]), RequestMeta(**result["meta"])


def llm_request(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[LLMResponse, RequestMeta]:
    """단일 LLM 요청 실행 (sync). llm_request_async의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        database=database,
        timings=timings,
        debug=debug,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[AsyncIterator[dict[str, Any]], dict[str, Any]]:
    """스트리밍 LLM 요청 (async). Phase 3: stored_files 만 받음."""
    t = _resolve_timings(timings)
    client = await _get_client()
    return await client.request_stream(
        llm_input,
        stored_files=stored_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=t["mdb_ttl"],
        database=database,
        context_cache_ttl=t["context_cache_ttl"],
        standby_ttl_seconds=t["standby_ttl"],
        http_timeout=t["http_timeout"],
        debug=debug,
    )


def llm_request_stream(
    llm_input: dict,
    *,
    stored_files: list[StoredFile] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[Iterator[dict[str, Any]], dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). Phase 3: stored_files 만 받음."""
    async def _collect() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        stream, meta = await llm_request_stream_async(
            llm_input,
            stored_files=stored_files,
            cache_mode=cache_mode,
            save_mode=save_mode,
            database=database,
            timings=timings,
            debug=debug,
        )
        events: list[dict[str, Any]] = []
        async for event in stream:
            events.append(event)
        return events, meta

    events, meta = asyncio.run(_collect())
    return iter(events), meta


async def done_check(
    llm_input: dict,
    *,
    files: list[FileKeyingMeta] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시(LLM 응답) 존재 여부 확인.

    files: FileKeyingMeta 리스트 (hashing 3필드만). bytes 불필요.
    """
    client = await _get_client()
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
    client = await _get_client()
    result = await client.fetch(cache_key, database=database)
    return LLMResponse(**result["result"]), RequestMeta(**result["meta"])


async def delete_cache_async(
    cache_ids: str | list[str],
    *,
    grace_seconds: int = 3,
) -> ExpireResult:
    """Context Cache 를 grace_seconds 후 만료시킨다 (async).

    이름은 delete 지만 내부적으로는 Gemini `caches.update(ttl=grace_seconds)` 로
    TTL 을 단축한다 — `caches.delete` 는 원래 TTL 그대로 청구되는 반면 update 는
    새 TTL 로 prorate 청구되기 때문 (storage 비용 절감). grace_seconds 동안
    in-flight 요청을 보호한 뒤 Gemini cache + Redis 가 만료된다.

    Args:
        cache_ids: 응답 `meta.context_cache_id` 로 받은 식별자. 단건이면 문자열
            하나, 여러 개면 리스트로 넘긴다. (단건도 결과는 리스트로 돌아온다.)
        grace_seconds: in-flight 요청 보호 grace period (1~3600, 기본 3).

    Returns:
        ExpireResult — `expired` (만료 성공, 또는 이미 없어진 cache 의 idempotent
        처리 포함), `failed` (Gemini 5xx·Redis 오류 등으로 만료 못 한 건).

    Raises:
        ValueError: cache_ids 가 비어있거나 grace_seconds 가 범위(1~3600) 밖일 때.
        ServiceConnectionError: llm-request 서비스에 연결 불가 / 응답 타임아웃.
        ServiceError: 서비스가 에러 응답을 반환 (예: error_type="validation").

    Note:
        개별 cache 의 만료 실패는 예외로 raise 되지 않고 `result.failed` 에
        담긴다 (여러 건 동시 처리 시 부분 실패 허용). 실제 삭제 여부가 중요하면
        호출 후 `result.failed` 를 반드시 확인할 것.
    """
    if isinstance(cache_ids, str):
        ids = [cache_ids]
    else:
        ids = list(cache_ids) if cache_ids else []
    if not ids:
        raise ValueError(
            "삭제할 cache_id 가 없습니다. cache_id 를 1개 이상 넣어주세요."
        )
    if not 1 <= grace_seconds <= 3600:
        raise ValueError(
            f"grace_seconds 는 1~3600초 사이여야 합니다 (받은 값: {grace_seconds})."
        )

    client = await _get_client()
    result = await client.delete_cache(ids, grace_seconds=grace_seconds)
    return ExpireResult(**result)


def delete_cache(
    cache_ids: str | list[str],
    *,
    grace_seconds: int = 3,
) -> ExpireResult:
    """Context Cache 를 grace_seconds 후 만료시킨다 (sync). delete_cache_async 의 동기 래퍼."""
    return asyncio.run(
        delete_cache_async(cache_ids, grace_seconds=grace_seconds)
    )
