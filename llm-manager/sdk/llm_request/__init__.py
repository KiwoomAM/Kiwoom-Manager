"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from llm_request import llm_request_async

    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        file_paths=["report.pdf"],
    )
"""

from __future__ import annotations

import os
from typing import Any

from llm_request._client import AsyncClient
from llm_request.checksum import FileChecksum, file_checksum
from llm_request.exceptions import SDKError, ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, RequestResult

__all__ = [
    "llm_request_async",
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


def _check_redis_file_cache(checksums: list[str]) -> list[str]:
    """Redis 파일 캐시에서 miss인 checksum 목록을 반환한다.

    Redis 연결이 없거나 실패하면 전부 miss로 처리한다.
    """
    try:
        from redis_handler import BaseJsonStore

        store = BaseJsonStore(prefix="file_cache")
        missing = []
        for cs in checksums:
            key = store._make_key("files_api", cs)
            if store._get_json(key) is None:
                missing.append(cs)
        return missing
    except Exception:
        return list(checksums)


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
    """단일 LLM 요청 실행 (async). (result, meta) 튜플 반환.

    기존 라이브러리 llm_request_async와 동일한 시그니처.
    내부에서 checksum 계산 + Redis 파일 캐시 조회 + 서버 호출.

    Args:
        llm_input: LLM 호출 파라미터 dict.
        file_paths: 로컬 파일 경로 리스트.
        cache_mode: MDB 캐시(LLM 응답) 조회 여부.
        save_mode: MDB 캐시(LLM 응답) 저장 여부.
        ttl_seconds: MDB 캐시 TTL (초).
        debug: 디버그 모드.
        database: MongoDB 데이터베이스명.
        context_cache_ttl: context cache TTL (초).

    Returns:
        (result dict, meta dict) 튜플.
    """
    import base64

    client = _get_client()
    file_checksums: list[FileChecksum] | None = None
    inline_files: list[dict] | None = None

    _INLINE_LIMIT = 20 * 1024 * 1024  # 20MB

    if file_paths:
        # 1. checksum 계산 (로컬)
        file_checksums = [file_checksum(p) for p in file_paths]

        # 2. 크기별 분류: ≤20MB → inline, >20MB → upload
        inline_files = []
        large_checksums: list[str] = []

        for i, fc in enumerate(file_checksums):
            if fc["size"] <= _INLINE_LIMIT:
                # ≤20MB: base64로 읽어서 request에 직접 포함
                with open(file_paths[i], "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                inline_files.append({
                    "checksum": fc["checksum"],
                    "filename": fc["filename"],
                    "mime_type": fc["mime_type"],
                    "data_base64": data,
                })
            else:
                large_checksums.append(fc["checksum"])

        # 3. >20MB 파일: Redis 파일 캐시 조회 → miss면 서버로 업로드
        if large_checksums:
            missing = _check_redis_file_cache(large_checksums)
            if missing:
                missing_indices = [
                    i for i, fc in enumerate(file_checksums)
                    if fc["checksum"] in missing and fc["size"] > _INLINE_LIMIT
                ]
                missing_paths = [file_paths[i] for i in missing_indices]
                missing_metas = [file_checksums[i] for i in missing_indices]

                await client.upload_files(
                    missing_paths,
                    missing_metas,
                    context_cache_ttl=context_cache_ttl,
                    model=llm_input.get("model"),
                    system_instruction=llm_input.get("system"),
                )

        if not inline_files:
            inline_files = None

    # 4. 서버에 LLM 요청
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
