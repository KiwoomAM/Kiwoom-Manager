"""SDK 요청/응답 모델."""

from __future__ import annotations

from typing import Any, TypedDict


class FileChecksum(TypedDict):
    """파일 checksum + 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int  # bytes
    mime_type: str


class RequestResult(TypedDict):
    """llm_request_async 서버 응답."""

    result: dict[str, Any]
    meta: dict[str, Any]


class DoneCheckResult(TypedDict):
    """done_check 서버 응답."""

    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_hit: bool
    collection: str
