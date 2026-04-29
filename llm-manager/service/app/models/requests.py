"""요청 DTO."""

from __future__ import annotations

from base64 import b64decode, b64encode
from typing import Annotated, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer


# JSON 직렬화 시 base64 문자열로 인코딩, 파싱 시 raw bytes로 디코딩.
# Pydantic v2의 `bytes` 타입은 JSON 모드에서 UTF-8 디코드를 시도해 바이너리는 실패하므로 필요.
#
# ⚠️ 의도된 중복: 동일 정의가 sdk/llm_request/models.py 에도 존재.
# SDK ↔ Service 는 독립 파이썬 패키지로 공유 모듈이 없으므로 양쪽에 같은 정의 유지.
# 수정 시 양쪽 **반드시** 동기화 (직렬화 동작이 SDK/Service 계약의 일부).
Base64Field = Annotated[
    bytes,
    BeforeValidator(lambda v: b64decode(v) if isinstance(v, str) else v),
    PlainSerializer(lambda b: b64encode(b).decode(), when_used="json", return_type=str),
]


class FileInput(BaseModel):
    """에이전트가 서버에 넘기는 파일 입력.

    file_path / resolved_via 는 에이전트가 세팅하지 않는다 (Service 내부에서 채움).
    - file_path: register_files 또는 file_resolver 가 file-manager 응답으로 채움
    - resolved_via (Phase 2): bytes 가 어느 경로로 확보됐는지 추적
        - "upload": data-mode (files=[..]) — 호출자 bytes 를 file-manager 에 신규 업로드
        - "primary": path-mode (stored_files=[..]) — file_path 1차 fetch 성공
        - "fallback": path-mode — file_path stale → check_cache 로 새 path 발견 후 fetch

    두 필드 모두 `_dump_files_meta` 가 dump 시 응답·MDB doc 에 노출.
    """

    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: Base64Field
    file_path: str | None = None
    resolved_via: str | None = None


class FileKeyingMeta(BaseModel):
    """/v1/done-check 용 경량 메타. hashing 3필드만 포함."""

    file_name: str
    file_size: int
    file_mtime: Union[str, int]


class StoredFile(BaseModel):
    """이미 file-manager 에 등록된 파일 참조 (Phase 2 입력).

    FileInput 의 5 메타 필드 + file_path 로 구성 — bytes 만 file_path 로 대체된 형태.
    Phase 1 ↔ Phase 2 caller mental model 대칭 (caller 는 Phase 1 응답의 6 필드를 그대로 다시 사용).

    서버는 이 값들을 신뢰하여 FileInput 으로 변환 + file-manager get_file 로는 bytes 만
    가져온다. mime/extension 의 권위는 처음 등록한 caller 에 있고, file-manager 도
    등록 시점 caller 값을 그대로 보관하므로 권위 차이 없음.

    extra="forbid": Phase 1 응답에 있는 server-fill 필드 (`resolved_via` 등) 를 caller 가
    실수로 함께 넘기면 ValidationError. 의도된 strict — 키 오타 / 잘못된 입력 즉시 잡기 (P2-4).

    ⚠️ 의도된 중복: sdk/llm_request/models.py 에도 동일 정의 존재.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str


class LLMRequestBody(BaseModel):
    """POST /v1/request 요청 바디.

    files: 에이전트가 제공하는 파일 리스트 (meta + bytes). 서버가 내부에서
    file-manager에 등록 후 파이프라인에 그대로 전달한다.

    stored_files (Phase 2): 이미 file-manager 에 등록된 파일 참조. 서버가
    file-manager 에서 fetch 하여 bytes 로딩 후 파이프라인에 합친다.
    """

    llm_input: dict
    files: list[FileInput] | None = None
    stored_files: list[StoredFile] | None = None
    cache_mode: bool = True
    save_mode: bool = True
    ttl_seconds: int | None = None
    database: str | None = None
    context_cache_ttl: int | None = None
    standby_ttl_seconds: int = 600
    debug: bool = False


class DoneCheckBody(BaseModel):
    """POST /v1/done-check 요청 바디."""

    llm_input: dict
    files: list[FileKeyingMeta] | None = None
    database: str | None = None


class FetchBody(BaseModel):
    """POST /v1/fetch 요청 바디."""

    cache_key: str
    database: str | None = None
