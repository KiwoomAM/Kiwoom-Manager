"""요청 DTO."""

from __future__ import annotations

from pydantic import BaseModel


class FileChecksumDTO(BaseModel):
    """파일 checksum 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int
    mime_type: str


class InlineFileDTO(BaseModel):
    """≤20MB 파일: base64로 직접 전달."""

    checksum: str
    filename: str
    mime_type: str
    data_base64: str  # base64 인코딩된 파일 데이터


class LLMRequestBody(BaseModel):
    """POST /v1/request 요청 바디."""

    llm_input: dict
    file_checksums: list[FileChecksumDTO] | None = None
    inline_files: list[InlineFileDTO] | None = None  # ≤20MB 파일 직접 포함
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
