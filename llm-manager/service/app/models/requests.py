"""요청 DTO."""

from __future__ import annotations

from pydantic import BaseModel

from file_manager import FileMeta


class LLMRequestBody(BaseModel):
    """POST /v1/request 요청 바디.

    file_metas: file_manager가 제공한 metadata 리스트.
    SDK는 파일 bytes를 직접 보내지 않는다 (file_manager 분리 설계).
    """

    llm_input: dict
    file_metas: list[FileMeta] | None = None
    cache_mode: bool = True
    save_mode: bool = True
    ttl_seconds: int | None = None
    debug: bool = False
    database: str | None = None
    context_cache_ttl: int | None = None
    standby_ttl_seconds: int = 600


class DoneCheckBody(BaseModel):
    """POST /v1/done-check 요청 바디."""

    llm_input: dict
    file_metas: list[FileMeta] | None = None
    database: str | None = None


class FetchBody(BaseModel):
    """POST /v1/fetch 요청 바디."""

    cache_key: str
    database: str | None = None
    as_stream: bool = False
