"""SDK 요청/응답 모델."""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Annotated, Any, TypedDict, Union

from pydantic import BaseModel, BeforeValidator, PlainSerializer

__all__ = [
    "FileInput",
    "FileKeyingMeta",
    "StoredFile",
    "RequestMeta",
    "RequestResult",
    "DoneCheckResult",
    "ExpireResult",
    "ExpireFailure",
]


# JSON 직렬화 시 base64 문자열로 인코딩, 파싱 시 raw bytes로 디코딩.
# Pydantic v2의 `bytes` 타입은 JSON 모드에서 UTF-8 디코드를 시도해 바이너리는 실패하므로 필요.
#
# ⚠️ 의도된 중복: 동일 정의가 service/app/models/requests.py 에도 존재.
# SDK ↔ Service 는 독립 파이썬 패키지로 공유 모듈이 없으므로 양쪽에 같은 정의 유지.
# 수정 시 양쪽 **반드시** 동기화 (직렬화 동작이 SDK/Service 계약의 일부).
Base64Field = Annotated[
    bytes,
    BeforeValidator(lambda v: b64decode(v) if isinstance(v, str) else v),
    PlainSerializer(lambda b: b64encode(b).decode(), when_used="json", return_type=str),
]


class FileInput(BaseModel):
    """/v1/request 에이전트가 보낼 파일 입력.

    에이전트가 세팅하는 5개 meta 필드 + bytes 로 구성.

    서버 DTO(`service/app/models/requests.py::FileInput`) 에는 `file_path` 필드가 추가로 있지만,
    이는 file-manager 등록 시 서버 내부에서 채우는 값으로 **에이전트는 세팅/참조하지 않는다**.
    따라서 SDK 모델은 이 필드를 정의하지 않는다 — SDK 가 JSON 직렬화 시 `file_path` 키가
    빠진 채로 전송되고, 서버 Pydantic 이 기본값 `None` 으로 받은 뒤 file_register 단계에서 채운다.
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


class StoredFile(BaseModel):
    """이미 file-manager 에 등록된 파일 참조.

    FileInput 의 5 메타 필드 + file_path — bytes 만 file_path 로 대체된 형태.
    caller 는 file-manager `register_or_get` 결과 6 필드 dict 를 그대로 받아 StoredFile 로 변환.

    extra="forbid": 응답에 박힌 server-fill 필드 (`resolved_via` 등) 를 실수로 넘기면 ValidationError.
    의도된 strict — 키 오타 / 잘못된 입력 즉시 잡기 (P2-4).

    ⚠️ 의도된 중복: service/app/models/requests.py 에도 동일 정의 존재.
    """

    model_config = {"extra": "forbid"}

    file_path: str
    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str


class RequestMeta(BaseModel):
    """llm_request_async 반환 meta 객체."""

    source: str
    cache_hit: bool
    cache_key: str | None = None
    metadata: dict[str, Any] = {}
    collection: str | None = None
    route_reason: str | None = None
    file_meta: list[dict] | None = None
    context_cache_id: str | None = None


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


class ExpireFailure(BaseModel):
    """expire_context_cache_async 응답의 실패 항목."""

    cache_id: str
    error: str


class ExpireResult(BaseModel):
    """expire_context_cache_async 반환.

    expired: 단축 성공 (Redis miss / Gemini 404 idempotent 포함).
    failed:  진짜 실패 (Gemini 5xx / 네트워크 등).
    """

    expired: list[str]
    failed: list[ExpireFailure]
