"""요청 DTO."""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Annotated, Union

from pydantic import BaseModel, BeforeValidator, PlainSerializer


# JSON 직렬화 시 base64 문자열로 인코딩, 파싱 시 raw bytes로 디코딩.
# Pydantic v2의 `bytes` 타입은 JSON 모드에서 UTF-8 디코드를 시도해 바이너리는 실패하므로 필요.
Base64Field = Annotated[
    bytes,
    BeforeValidator(lambda v: b64decode(v) if isinstance(v, str) else v),
    PlainSerializer(lambda b: b64encode(b).decode(), when_used="json", return_type=str),
]


class FileInput(BaseModel):
    """에이전트가 서버에 넘기는 파일 입력.

    file_path는 없음 — file-manager가 업로드 시 부여하므로 에이전트는 알 수 없다.
    """

    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: Base64Field


class FileKeyingMeta(BaseModel):
    """/v1/done-check 용 경량 메타. hashing 3필드만 포함."""

    file_name: str
    file_size: int
    file_mtime: Union[str, int]


class LLMRequestBody(BaseModel):
    """POST /v1/request 요청 바디.

    files: 에이전트가 제공하는 파일 리스트 (meta + bytes). 서버가 내부에서
    file-manager에 등록 후 파이프라인에 그대로 전달한다.
    """

    llm_input: dict
    files: list[FileInput] | None = None
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
    files: list[FileKeyingMeta] | None = None
    database: str | None = None


class FetchBody(BaseModel):
    """POST /v1/fetch 요청 바디."""

    cache_key: str
    database: str | None = None
    as_stream: bool = False
