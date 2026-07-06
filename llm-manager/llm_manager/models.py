"""llm-manager 공개/내부 공용 모델.

서버 시절 sdk/llm_request/models.py + service/app/models/requests.py 로 나뉘어
중복 유지하던 정의를 단일 모듈로 통합. 공개 표면은 `__init__.py` 의 re-export +
`__all__` 로 큐레이션한다 (FileInput 은 내부 전용이라 re-export 하지 않음).
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

__all__ = [
    "FileInput",
    "FileKeyingMeta",
    "StoredFile",
    "ResponseMeta",
    "ResponseResult",
    "DoneCheckResult",
    "ExpireResult",
    "ExpireFailure",
]


# JSON 직렬화 시 base64 문자열로 인코딩, 파싱 시 raw bytes로 디코딩.
# Pydantic v2의 `bytes` 타입은 JSON 모드에서 UTF-8 디코드를 시도해 바이너리는 실패하므로 필요.
Base64Field = Annotated[
    bytes,
    BeforeValidator(lambda v: b64decode(v) if isinstance(v, str) else v),
    PlainSerializer(lambda b: b64encode(b).decode(), when_used="json", return_type=str),
]


class FileInput(BaseModel):
    """내부 파일 입력 (공개 표면 아님 — __init__ 에서 re-export 하지 않음).

    Phase 3 부터 agent 는 직접 FileInput 을 만들지 않는다 (`stored_files=[StoredFile]` 만 받음).
    stored_file_resolver 가 StoredFile → FileInput 으로 변환.

    `data` 는 진입 시 빈 bytes(`b""`) 로 두고, 실제 내용은 resolver 에서 lazy fetch.
    `file_path`: stored_files 의 ref.file_path (stale 시 check_cache 로 갱신).
    `resolved_via`: "primary"(1차 fetch) | "fallback"(check_cache 경유).
    `page_count`: PDF 페이지 수 — 업로드 시(bytes 보유 시점) 계산해 stash. context cache
        too-small 선제 게이트(§4.4.1)의 토큰 어림에 사용. PDF 외/미상이면 None.
    `image_tokens`: 이미지 Gemini 타일 토큰 어림 — page_count 와 같은 목적/시점(업로드 시
        stash)으로 이미지의 too-small 게이트용. 이미지 외/미상이면 None.
    """

    file_name: str
    file_size: int
    file_mtime: str | int
    mime_type: str
    extension: str
    data: Base64Field = b""
    file_path: str | None = None
    resolved_via: str | None = None
    page_count: int | None = None
    image_tokens: int | None = None


class FileKeyingMeta(BaseModel):
    """done_check 용 경량 메타. hashing 3필드만."""

    file_name: str
    file_size: int
    file_mtime: str | int


class StoredFile(BaseModel):
    """이미 file-manager 에 등록된 파일 참조.

    FileInput 의 5 메타 필드 + file_path — bytes 만 file_path 로 대체된 형태.
    caller 는 file-manager `register_or_get` 결과 6 필드 dict 를 그대로 받아 StoredFile 로 변환.

    extra="forbid": 응답에 박힌 server-fill 필드(`resolved_via` 등)를 실수로 넘기면 ValidationError.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    file_name: str
    file_size: int
    file_mtime: str | int
    mime_type: str
    extension: str


class ResponseMeta(BaseModel):
    """llm_request_async 반환 meta 객체."""

    source: str
    cache_hit: bool
    cache_key: str | None = None
    metadata: dict[str, Any] = {}
    collection: str | None = None
    route_reason: str | None = None
    file_meta: list[dict] | None = None
    context_cache_id: str | None = None


class ResponseResult(TypedDict):
    """raw (result, meta) dict 쌍."""

    result: dict[str, Any]
    meta: dict[str, Any]


class DoneCheckResult(TypedDict):
    """done_check 반환."""

    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_hit: bool
    collection: str


class ExpireFailure(BaseModel):
    """delete_cache 응답의 실패 항목 (Gemini 5xx / 네트워크 등)."""

    cache_id: str
    error: str


class ExpireResult(BaseModel):
    """delete_cache 반환.

    expired: 단축 성공 (Redis miss / Gemini 404 idempotent 포함).
    failed:  진짜 실패 (Gemini 5xx / 네트워크 등).
    """

    expired: list[str]
    failed: list[ExpireFailure]
