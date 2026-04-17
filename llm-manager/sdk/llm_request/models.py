"""SDK 요청/응답 모델."""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Annotated, Any, TypedDict, Union

from pydantic import BaseModel, BeforeValidator, PlainSerializer

__all__ = [
    "FileInput",
    "FileKeyingMeta",
    "RequestMeta",
    "RequestResult",
    "DoneCheckResult",
]


# JSON 직렬화 시 base64 문자열로 인코딩, 파싱 시 raw bytes로 디코딩.
# Pydantic v2의 `bytes` 타입은 JSON 모드에서 UTF-8 디코드를 시도해 바이너리는 실패하므로 필요.
Base64Field = Annotated[
    bytes,
    BeforeValidator(lambda v: b64decode(v) if isinstance(v, str) else v),
    PlainSerializer(lambda b: b64encode(b).decode(), when_used="json", return_type=str),
]


class FileInput(BaseModel):
    """/v1/request 에이전트가 보낼 파일 입력.

    file_path는 없음 — file-manager가 업로드 시 부여하므로 에이전트는 알 수 없다.
    서버 DTO와 동일 필드 (service/app/models/requests.py::FileInput).
    """

    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: Base64Field


class FileKeyingMeta(BaseModel):
    """/v1/done-check 경량 메타. hashing 3필드만."""

    file_name: str
    file_size: int
    file_mtime: Union[str, int]


class RequestMeta(BaseModel):
    """llm_request_async 반환 meta 객체."""

    source: str
    cache_hit: bool
    cache_key: str | None = None
    metadata: dict[str, Any] = {}
    collection: str | None = None
    route_reason: str | None = None


class RequestResult(TypedDict):
    """llm_request_async 서버 응답 (raw)."""

    result: dict[str, Any]
    meta: dict[str, Any]


class DoneCheckResult(TypedDict):
    """done_check 서버 응답."""

    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_hit: bool
    collection: str
