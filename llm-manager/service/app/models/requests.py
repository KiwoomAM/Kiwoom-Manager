"""요청 DTO."""

from __future__ import annotations

from pydantic import BaseModel


class FileChecksumDTO(BaseModel):
    """파일 checksum 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int
    mime_type: str


class LLMRequestBody(BaseModel):
    """POST /v1/request 요청 바디."""

    llm_input: dict
    file_checksums: list[FileChecksumDTO] | None = None
    cache_mode: bool = True
    save_mode: bool = True
    ttl_seconds: int | None = None
    debug: bool = False
    database: str | None = None
    context_cache_ttl: int | None = None


class DoneCheckBody(BaseModel):
    """POST /v1/done-check 요청 바디."""

    llm_input: dict
    file_checksums: list[str] | None = None
    database: str | None = None


class FetchBody(BaseModel):
    """POST /v1/fetch 요청 바디."""

    cache_key: str
    database: str | None = None
    as_stream: bool = False
