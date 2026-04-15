"""응답 DTO."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LLMRequestResponse(BaseModel):
    """POST /v1/request 응답."""

    result: dict[str, Any]  # LLMResponse.model_dump()
    meta: dict[str, Any]


class DoneCheckResponse(BaseModel):
    """POST /v1/done-check 응답."""

    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_hit: bool
    collection: str


class FetchResponse(BaseModel):
    """POST /v1/fetch 응답."""

    result: dict[str, Any]
    meta: dict[str, Any]


class ErrorResponse(BaseModel):
    """에러 응답."""

    error: str
    detail: str | None = None
    error_type: str = "handler"
    request_id: str = ""
