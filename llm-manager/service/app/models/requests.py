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
    """서버 내부에서 사용하는 파일 입력.

    Phase 3 부터 agent 는 직접 FileInput 을 만들지 않는다 (`stored_files=[StoredFile]` 만 받음).
    file_resolver 가 StoredFile → FileInput 으로 변환.

    `data` 는 진입 시 비어있는 빈 bytes (`b""`) 로 두고, 실제 파일 내용은 4·5단계 (resolver.py) 에서
    필요할 때 file-manager 에서 가져와 채운다 (lazy fetch). 4·5단계 진입 전까진 메타 정보만 사용 —
    cache_key 계산 / dedup standby 등 hashing 3 메타로 충분.

    `file_path`:
    - 정상: file_resolver 가 stored_files 의 ref.file_path 그대로 복사 (agent 입력 값)
    - fallback (stale): 4·5단계 lazy fetch 시 file-manager 가 404 → check_cache 응답의
                        새 file_path 로 갱신

    `resolved_via` (Phase 2): bytes 가 어느 경로로 확보됐는지 추적
    - "primary": file_path 1차 fetch 성공
    - "fallback": file_path stale → check_cache 로 새 path 발견 후 fetch

    두 필드 모두 `_dump_files_meta` 가 dump 시 응답·MDB doc 에 노출.
    """

    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: Base64Field = b""
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

    Phase 3: `files` 인자 (Method 1, bytes 직접 전송) 폐기. `stored_files` 만 받는다.
    `extra='forbid'` — 옛 SDK 가 실수로 `files` 보내면 명시적 ValidationError (silent drop 방지).

    stored_files: 이미 file-manager 에 등록된 파일 참조. agent 가 file-manager SDK 의
    `register_or_get` 으로 등록한 후 받은 6 필드 dict 를 StoredFile 로 변환해 전달.
    """

    model_config = ConfigDict(extra="forbid")

    llm_input: dict
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
