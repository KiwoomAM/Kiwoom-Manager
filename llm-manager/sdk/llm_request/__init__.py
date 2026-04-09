"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from llm_request import llm_request_async

    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        file_paths=["report.pdf"],
    )
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

from llm_request._client import AsyncClient
from llm_request.checksum import FileChecksum, file_checksum
from llm_request.exceptions import SDKError, ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, RequestResult

__all__ = [
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "done_check",
    "fetch_by_cache_key",
    "configure",
    "file_checksum",
    "FileChecksum",
    "SDKError",
    "ServiceConnectionError",
    "ServiceError",
]

_instance: AsyncClient | None = None
_file_cache_store = None

_INLINE_LIMIT = 20 * 1024 * 1024  # 20MB


def _resolve_base_url() -> str:
    """서비스 URL을 환경변수에서 읽기."""
    return os.getenv("LLM_REQUEST_URL", "http://localhost:8101")


def _get_client() -> AsyncClient:
    """싱글톤 AsyncClient 반환."""
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


def configure(base_url: str | None = None, timeout: float = 120.0) -> None:
    """SDK 클라이언트를 수동 설정.

    Args:
        base_url: llm-request 서비스 URL (기본: 환경변수 또는 localhost:8101)
        timeout: HTTP 타임아웃 (초)
    """
    global _instance
    url = base_url or _resolve_base_url()
    _instance = AsyncClient(base_url=url, timeout=timeout)


def _get_file_cache_store():
    """Redis file_cache 싱글톤 반환. 연결 실패 시 None."""
    global _file_cache_store
    if _file_cache_store is None:
        try:
            from redis_handler import BaseJsonStore
            _file_cache_store = BaseJsonStore(prefix="file_cache")
        except Exception:
            return None
    return _file_cache_store


def _check_redis_file_cache(checksums: list[str]) -> list[str]:
    """Redis 파일 캐시에서 miss인 checksum 목록을 반환한다.

    >20MB 파일의 checksum만 전달해야 한다 (≤20MB는 inline 처리되므로 Redis 불필요).
    Redis 연결이 없거나 실패하면 전부 miss로 처리한다.
    """
    store = _get_file_cache_store()
    if store is None:
        return list(checksums)
    try:
        missing = []
        for cs in checksums:
            key = store._make_key("files_api", cs)
            if store._get_json(key) is None:
                missing.append(cs)
        return missing
    except Exception:
        return list(checksums)


# ── 파일 처리 공통 헬퍼 ────────────────────────────────────────

def _prepare_files(
    file_paths: list[str] | None,
) -> tuple[list[FileChecksum] | None, list[dict] | None]:
    """파일 경로에서 checksum 계산 + inline/large 분류."""
    if not file_paths:
        return None, None

    import base64

    file_checksums = [file_checksum(p) for p in file_paths]
    inline_files: list[dict] = []

    for i, fc in enumerate(file_checksums):
        if fc["size"] <= _INLINE_LIMIT:
            with open(file_paths[i], "rb") as f:
                data = base64.b64encode(f.read()).decode()
            inline_files.append({
                "checksum": fc["checksum"],
                "filename": fc["filename"],
                "mime_type": fc["mime_type"],
                "data_base64": data,
            })

    return file_checksums, inline_files or None


async def _upload_missing_files(
    client: AsyncClient,
    file_paths: list[str],
    file_checksums: list[FileChecksum],
    inline_files: list[dict] | None,
    context_cache_ttl: int | None,
    llm_input: dict,
) -> None:
    """>20MB 파일 중 Redis에 없는 것만 서버로 업로드."""
    inline_checksums = {f["checksum"] for f in inline_files} if inline_files else set()
    large_checksums = [
        fc["checksum"] for fc in file_checksums
        if fc["size"] > _INLINE_LIMIT and fc["checksum"] not in inline_checksums
    ]

    if not large_checksums:
        return

    missing = _check_redis_file_cache(large_checksums)
    if not missing:
        return

    missing_indices = [
        i for i, fc in enumerate(file_checksums)
        if fc["checksum"] in missing and fc["size"] > _INLINE_LIMIT
    ]
    await client.upload_files(
        [file_paths[i] for i in missing_indices],
        [file_checksums[i] for i in missing_indices],
        context_cache_ttl=context_cache_ttl,
        model=llm_input.get("model"),
        system_instruction=llm_input.get("system"),
    )


# ── Public API ─────────────────────────────────────────────────

async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (result, meta) 튜플 반환."""
    client = _get_client()
    file_checksums, inline_files = _prepare_files(file_paths)

    if file_paths and file_checksums:
        await _upload_missing_files(
            client, file_paths, file_checksums, inline_files,
            context_cache_ttl, llm_input,
        )

    result = await client.request(
        llm_input,
        file_checksums=file_checksums,
        inline_files=inline_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    )

    return result["result"], result["meta"]


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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). llm_request_async의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    ))


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
) -> AsyncIterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (async). SSE 이벤트를 파싱하여 dict로 yield.

    Returns:
        AsyncIterator of event dicts:
        - {"type": "chunk", "text": "...", "finish_reason": None}
        - {"type": "chunk", "text": "...", "finish_reason": "STOP", "usage": {...}}
    """
    client = _get_client()
    file_checksums, inline_files = _prepare_files(file_paths)

    if file_paths and file_checksums:
        await _upload_missing_files(
            client, file_paths, file_checksums, inline_files,
            context_cache_ttl, llm_input,
        )

    async for event in client.request_stream(
        llm_input,
        file_checksums=file_checksums,
        inline_files=inline_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    ):
        yield event


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
) -> Iterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). llm_request_stream_async의 동기 래퍼.

    Note: 내부적으로 새 event loop을 생성하여 실행. 기존 event loop 안에서는 사용 불가.
    """
    async def _collect():
        results = []
        async for event in llm_request_stream_async(
            llm_input,
            file_paths=file_paths,
            cache_mode=cache_mode,
            save_mode=save_mode,
            ttl_seconds=ttl_seconds,
            debug=debug,
            database=database,
            context_cache_ttl=context_cache_ttl,
        ):
            results.append(event)
        return results

    return iter(asyncio.run(_collect()))


async def done_check(
    llm_input: dict,
    *,
    file_checksums: list[str] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시(LLM 응답) 존재 여부 확인."""
    client = _get_client()
    return await client.done_check(
        llm_input,
        file_checksums=file_checksums,
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
