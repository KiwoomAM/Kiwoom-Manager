# llm-request 서버화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** llm-request 라이브러리를 SDK + Service로 분리하여 독립 서버로 운영

**Architecture:** 기존 비즈니스 로직(runner, keying, store 등)을 Service의 logic/으로 이동하고, FastAPI 래퍼를 씌운다. SDK는 checksum 계산 + Redis 파일 캐시 조회 + httpx HTTP 클라이언트로 구성. llm-handler SDK/Service 패턴을 따른다.

**Tech Stack:** FastAPI, pydantic-settings, httpx, pytest

---

## 구현 후 변경 이력

> Task 1~11 구현 완료 후 코드 리뷰를 통해 발견된 개선 사항. 계획 문서의 코드 예시는 현재 코드 기준으로 갱신됨.

| 항목 | 변경 내용 |
|------|----------|
| runner.py 함수명 | `_prepare`/`_finalize`/`_build_call_input` → `prepare`/`finalize`/`build_call_input` (public 전환) |
| 라우터 inline 파일 | `InlineFileDTO` + base64 디코딩 → Part 변환 로직 추가 |
| SDK inline 분기 | ≤20MB inline (base64 body) / >20MB upload 분기 추가 |
| SDK Redis 싱글톤 | `_check_redis_file_cache`에서 매번 생성 → `_get_file_cache_store()` lazy 싱글톤 |
| SDK _client.py | timeout 처리 추가, 파일 close를 `opened` 리스트로 개선 |
| keying.py | canonicalize 완전 실패 시 `return "{}"` → `raise ValueError` |
| cache_store.py | `FILES_API_TTL` 48h → 47h (만료 URI 방지) |
| splitter.py | 병합 기준 50MB → 45MB (`_MERGE_LIMIT_MB`), fitz.open에 try/finally 추가 |
| `_build_call_input_from_cache` | `async def` → `def` (Redis/MDB가 sync이므로) |
| **Task 12 추가** | stream 엔드포인트 + SDK 나머지 3함수 + runner.py dead code 정리 |

---

## 파일 구조

```
packages/llm-request/
├── pyproject.toml                       # uv workspace root (기존 것 → workspace로 변환)
├── sdk/
│   ├── pyproject.toml                   # SDK 패키지 설정
│   └── llm_request/
│       ├── __init__.py                  # public API (llm_request_async 등)
│       ├── _client.py                   # AsyncClient (httpx)
│       ├── checksum.py                  # file_checksum() — 기존 코드 이동
│       ├── models.py                    # 요청/응답 타입 (SDK용)
│       └── exceptions.py               # SDK 예외 클래스
├── service/
│   ├── pyproject.toml                   # Service 패키지 설정
│   ├── Dockerfile                       # 컨테이너 빌드
│   └── app/
│       ├── main.py                      # FastAPI 앱
│       ├── config.py                    # pydantic-settings (port=8101)
│       ├── core/
│       │   ├── __init__.py
│       │   ├── errors.py               # LLMRequestServiceError 계층
│       │   └── providers.py            # MDB/Redis/LLM-handler 연결 싱글톤
│       ├── models/
│       │   ├── __init__.py
│       │   ├── requests.py             # 요청 DTO
│       │   └── responses.py            # 응답 DTO
│       ├── routers/
│       │   ├── __init__.py
│       │   └── request.py              # /v1/request, /v1/files, /v1/done-check, /v1/fetch
│       └── logic/                       # 기존 llm_request/ 코드 이동
│           ├── __init__.py
│           ├── runner.py                # _prepare, _finalize (file_meta 직접 수신 가능하도록 수정)
│           ├── keying.py                # 그대로 이동
│           ├── done_check.py            # 그대로 이동
│           ├── store.py                 # 그대로 이동
│           ├── meta.py                  # 그대로 이동
│           ├── orchestrator.py          # 그대로 이동
│           └── file_handler/
│               ├── __init__.py
│               ├── resolver.py          # 그대로 이동
│               ├── cache_store.py       # 그대로 이동
│               ├── uploader.py          # 그대로 이동
│               └── splitter.py          # 그대로 이동
├── plans/
└── tests/
    ├── service/                         # Service 테스트
    │   ├── conftest.py
    │   ├── test_router_request.py       # 엔드포인트 테스트
    │   └── logic/                       # 기존 테스트 이동 (import 경로만 변경)
    │       ├── test_keying.py
    │       ├── test_checksum.py
    │       ├── test_done_check.py
    │       ├── test_store.py
    │       ├── test_meta.py
    │       ├── test_orchestrator.py
    │       ├── test_resolver.py
    │       ├── test_cache_store.py
    │       ├── test_uploader.py
    │       └── test_splitter.py
    └── sdk/                             # SDK 테스트
        ├── conftest.py
        └── test_client.py               # HTTP 호출 mocking 테스트
```

## 핵심 변경 사항

### runner.py 수정

`_prepare()`에 `file_meta` 파라미터 추가. 서버는 SDK에서 받은 checksum으로 `file_meta`를 직접 전달하고, 기존 라이브러리 호환을 위해 `file_paths`도 유지.

```python
# 변경 전
def _prepare(llm_input, *, file_paths=None, ...):
    if cache_mode or save_mode:
        file_meta = _resolve_file_meta(file_paths)

# 변경 후
def _prepare(llm_input, *, file_paths=None, file_meta=None, ...):
    if cache_mode or save_mode:
        if file_meta is None:
            file_meta = _resolve_file_meta(file_paths)
```

### 파일 업로드 흐름

```
SDK:
  1. checksum 계산 (로컬)
  2. Redis 파일 캐시 조회 (로컬)
  3. miss인 파일 → POST /v1/files (multipart upload)
  4. POST /v1/request {llm_input, file_checksums}

Service /v1/files:
  1. 파일 수신 → 임시 저장
  2. resolve_files() 실행 (Files API 업로드 등)
  3. Redis 파일 캐시 저장
  4. 임시 파일 삭제

Service /v1/request:
  1. file_checksums → keying_input 구성
  2. done_check (MDB 캐시 조회)
  3. HIT → 즉시 반환
  4. MISS → Redis 파일 캐시에서 URI 조회 → call_input 구성 → LLM 호출 → 저장
```

---

## Task 1: Service 디렉토리 생성 + 로직 이동

**Files:**
- Create: `service/app/__init__.py`, `service/app/logic/__init__.py`, `service/app/logic/file_handler/__init__.py`
- Move: `llm_request/*.py` → `service/app/logic/`
- Move: `llm_request/file_handler/*.py` → `service/app/logic/file_handler/`

- [ ] **Step 1: service/app/logic/ 디렉토리 생성**

```bash
mkdir -p packages/llm-request/service/app/logic/file_handler
touch packages/llm-request/service/__init__.py
touch packages/llm-request/service/app/__init__.py
touch packages/llm-request/service/app/logic/__init__.py
touch packages/llm-request/service/app/logic/file_handler/__init__.py
```

- [ ] **Step 2: 기존 로직 파일 복사**

기존 `llm_request/` 파일들을 `service/app/logic/`으로 복사한다. 원본은 아직 삭제하지 않는다 (SDK 작업 시 checksum.py 이동용).

```bash
cd packages/llm-request
cp llm_request/runner.py service/app/logic/
cp llm_request/keying.py service/app/logic/
cp llm_request/done_check.py service/app/logic/
cp llm_request/store.py service/app/logic/
cp llm_request/meta.py service/app/logic/
cp llm_request/orchestrator.py service/app/logic/
cp llm_request/file_handler/resolver.py service/app/logic/file_handler/
cp llm_request/file_handler/cache_store.py service/app/logic/file_handler/
cp llm_request/file_handler/uploader.py service/app/logic/file_handler/
cp llm_request/file_handler/splitter.py service/app/logic/file_handler/
```

- [ ] **Step 3: import 경로 수정 — logic 모듈 내부**

모든 상대 import를 `app.logic` 기준으로 변경한다.

`service/app/logic/file_handler/__init__.py`:
```python
"""file_handler: 파일 업로드/캐싱 자동화 모듈."""

from .resolver import ResolvedFiles, resolve_files

__all__ = ["ResolvedFiles", "resolve_files"]
```

`service/app/logic/runner.py` — import 변경:
```python
# 변경 전
from .checksum import file_checksum as _compute_checksum
from .done_check import DoneCheckResult, done_check
from .file_handler import resolve_files
from .meta import build_meta
from .store import save_result

# 변경 후
from app.logic.done_check import DoneCheckResult, done_check
from app.logic.file_handler import resolve_files
from app.logic.meta import build_meta
from app.logic.store import save_result
```

주의: `_compute_checksum` import 제거. 서버에서는 checksum을 SDK에서 받는다.

`service/app/logic/done_check.py` — import 변경:
```python
# 변경 전
from .keying import canonicalize_input, make_cache_key
from .store import cache_get

# 변경 후
from app.logic.keying import canonicalize_input, make_cache_key
from app.logic.store import cache_get
```

`service/app/logic/orchestrator.py` — import 변경:
```python
# 변경 전
from .runner import llm_request_async, llm_request_stream_async

# 변경 후
from app.logic.runner import llm_request_async, llm_request_stream_async
```

`service/app/logic/file_handler/resolver.py` — import 변경:
```python
# 변경 전
from ..checksum import FileChecksum
from . import cache_store
from llm_handler.aio import upload_file
from .uploader import create_context_cache, upload_pdf_parts

# 변경 후
from app.logic.file_handler import cache_store
from app.logic.file_handler.uploader import create_context_cache, upload_pdf_parts
from llm_handler.aio import upload_file
from llm_handler.models import FileRef, Part
```

`FileChecksum` 타입은 더 이상 checksum.py에서 import하지 않고, service 내에서 TypedDict로 재정의한다 (SDK의 checksum.py와 중복이지만, 서로 독립적인 패키지이므로).

`service/app/logic/file_handler/cache_store.py` — import 변경:
```python
# 변경 전
from ..keying import canonicalize_llm_input, make_cache_key

# 변경 후
from app.logic.keying import canonicalize_llm_input, make_cache_key
```

- [ ] **Step 4: runner.py에 file_meta 직접 수신 파라미터 추가**

`service/app/logic/runner.py`의 `_prepare()`와 public 함수들에 `file_meta` 파라미터를 추가한다.

```python
# _resolve_file_meta 함수는 서버에서 불필요 → 제거

def _prepare(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    file_meta: list[dict] | None = None,  # 추가: SDK에서 받은 checksum 직접 전달
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> _Prepared:
    meta = build_meta(llm_input)
    requested_at = datetime.now(UTC)

    if cache_mode or save_mode:
        if file_meta is None and file_paths:
            raise ValueError("서버 모드에서는 file_meta를 직접 전달해야 합니다")

    # keying용 입력: file_meta가 있으면 checksum만 주입
    if file_meta:
        keying_input = {**llm_input, "file_checksums": [m["checksum"] for m in file_meta]}
    else:
        keying_input = llm_input

    # 이하 기존 로직 동일...
```

`llm_request_async()`에도 `file_meta` 파라미터 추가:
```python
async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    file_meta: list[dict] | None = None,  # 추가
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    prep = _prepare(
        llm_input,
        file_paths=file_paths,
        file_meta=file_meta,  # 추가
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        return prep.cached_output

    call_input = prep.llm_input
    if prep.file_meta and prep.file_paths:
        resolved = await resolve_files(
            prep.file_meta,
            prep.file_paths,
            model=prep.llm_input.get("model"),
            system_instruction=prep.llm_input.get("system"),
            context_cache_ttl=prep.context_cache_ttl,
        )
        call_input = _build_call_input(prep.llm_input, resolved)

    res = await aio_call(**call_input)
    return _finalize(prep, res, database=database)
```

- [ ] **Step 5: FileChecksum TypedDict를 logic에 재정의**

`service/app/logic/models.py` (신규 생성):
```python
"""logic 내부에서 사용하는 공유 타입."""

from __future__ import annotations

from typing import TypedDict


class FileChecksum(TypedDict):
    """파일 checksum + 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int  # bytes
    mime_type: str
```

`service/app/logic/file_handler/resolver.py`에서 import 변경:
```python
# 변경 전
from ..checksum import FileChecksum

# 변경 후
from app.logic.models import FileChecksum
```

- [ ] **Step 6: 커밋**

```bash
git add packages/llm-request/service/
git commit -m "feat(llm-request): service 디렉토리 생성 + 기존 로직 이동"
```

---

## Task 2: Service config + core

**Files:**
- Create: `service/app/config.py`
- Create: `service/app/core/__init__.py`
- Create: `service/app/core/errors.py`
- Create: `service/app/core/providers.py`

- [ ] **Step 1: config.py 작성**

```python
"""Service 설정 (환경변수 기반)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수로부터 설정을 읽어오는 클래스."""

    # 외부 서비스 URL
    mdb_handler_url: str = "http://localhost:8300"
    llm_handler_url: str = "http://localhost:8100"
    redis_url: str = "redis://localhost:6379/0"

    # MongoDB
    mongodb_database: str = "kw_ai"

    # Server
    host: str = "0.0.0.0"
    port: int = 8101

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
```

- [ ] **Step 2: core/errors.py 작성**

llm-handler의 errors.py 패턴을 따른다.

```python
"""표준화된 에러 타입."""


class LLMRequestServiceError(Exception):
    """llm-request Service 기본 에러."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "handler") -> None:
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


class CacheError(LLMRequestServiceError):
    """캐시 조회/저장 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="cache")


class FileUploadError(LLMRequestServiceError):
    """파일 업로드 처리 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="file")


class ValidationError(LLMRequestServiceError):
    """요청 검증 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="validation")
```

- [ ] **Step 3: core/providers.py 작성**

```python
"""외부 서비스 연결 관리."""

import os

from app.config import settings


def configure_handlers() -> None:
    """MDB/Redis/LLM handler SDK를 서비스 설정으로 초기화.

    FastAPI lifespan에서 호출한다.
    """
    # mdb-handler SDK 설정
    os.environ.setdefault("MDB_HANDLER_URL", settings.mdb_handler_url)
    from mdb_handler import MongoDBHandler
    MongoDBHandler.reset()

    # redis-handler SDK 설정
    os.environ.setdefault("REDIS_URL", settings.redis_url)
    from redis_handler import init_redis
    init_redis()

    # llm-handler SDK 설정
    os.environ.setdefault("LLM_HANDLER_URL", settings.llm_handler_url)
    import llm_handler
    import llm_handler.aio
    llm_handler.configure()
    llm_handler.aio.configure()

    # MongoDB database 환경변수 설정 (store.py에서 사용)
    os.environ.setdefault("MONGODB_DATABASE", settings.mongodb_database)
```

- [ ] **Step 4: core/__init__.py 작성**

```python
```

빈 파일.

- [ ] **Step 5: 커밋**

```bash
git add packages/llm-request/service/app/config.py packages/llm-request/service/app/core/
git commit -m "feat(llm-request): service config + core (errors, providers)"
```

---

## Task 3: Service models (DTO)

**Files:**
- Create: `service/app/models/__init__.py`
- Create: `service/app/models/requests.py`
- Create: `service/app/models/responses.py`

- [ ] **Step 1: models/__init__.py 작성**

```python
```

빈 파일.

- [ ] **Step 2: models/requests.py 작성**

```python
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
```

- [ ] **Step 3: models/responses.py 작성**

```python
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


class FileUploadResponse(BaseModel):
    """POST /v1/files 응답."""

    uploaded: list[str]  # 업로드된 file checksum 리스트
    message: str = "ok"


class ErrorResponse(BaseModel):
    """에러 응답."""

    error: str
    detail: str | None = None
    error_type: str = "handler"
    request_id: str = ""
```

- [ ] **Step 4: 커밋**

```bash
git add packages/llm-request/service/app/models/
git commit -m "feat(llm-request): service request/response DTO 모델"
```

---

## Task 4: Service router

**Files:**
- Create: `service/app/routers/__init__.py`
- Create: `service/app/routers/request.py`

- [ ] **Step 1: routers/__init__.py 작성**

```python
```

빈 파일.

- [ ] **Step 2: routers/request.py 작성 — /v1/request 엔드포인트**

> **구현 후 변경 사항 (현재 코드 기준):**
> - `_prepare` → `prepare`, `_finalize` → `finalize`, `_build_call_input` → `build_call_input` (public 전환)
> - `_build_call_input_from_cache`는 `def` (동기). Redis/MDB가 sync이므로 async 불필요.
> - `InlineFileDTO` + inline 파일 처리 추가 (≤20MB base64 디코딩 → Part 변환)
> - `models/requests.py`에 `InlineFileDTO` 클래스 추가

```python
"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, UploadFile

from app.core.errors import FileUploadError, ValidationError
from app.logic.done_check import done_check
from app.logic.file_handler import resolve_files
from app.logic.file_handler.cache_store import (
    find_context_cache,
    find_files_api,
    make_context_cache_key,
)
from app.logic.runner import build_call_input, finalize, prepare
from app.logic.store import fetch_by_cache_key
from app.models.requests import DoneCheckBody, FetchBody, LLMRequestBody
from app.models.responses import (
    DoneCheckResponse,
    FetchResponse,
    FileUploadResponse,
    LLMRequestResponse,
)

from llm_handler.aio import call as aio_call
from llm_handler.models import Part

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/request", response_model=LLMRequestResponse)
async def handle_request(body: LLMRequestBody) -> LLMRequestResponse:
    """LLM 요청 (MDB 캐시 조회 → 호출 → 저장).

    SDK에서 file_checksums를 전달하면 서버가 캐시 키에 반영한다.
    - inline_files: ≤20MB 파일은 base64로 직접 포함 (Redis/Files API 불필요)
    - file_checksums만: >20MB 파일은 /v1/files로 사전 업로드 필요
    """
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    prep = prepare(
        body.llm_input,
        file_meta=file_meta,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        debug=body.debug,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
    )

    # MDB 캐시 히트
    if prep.cached_output:
        response, meta = prep.cached_output
        return LLMRequestResponse(result=response.model_dump(), meta=meta)

    # MDB 캐시 미스 → LLM 호출
    call_input = dict(prep.llm_input)

    # ── inline 파일 처리 (≤20MB, base64 → Part) ──
    inline_parts: list = []
    inline_checksums: set[str] = set()
    if body.inline_files:
        import base64
        for f in body.inline_files:
            raw = base64.b64decode(f.data_base64)
            inline_parts.append(Part.from_bytes(data=raw, mime_type=f.mime_type))
            inline_checksums.add(f.checksum)

    # ── >20MB 파일: Redis 파일 캐시에서 URI 조회 ──
    if prep.file_meta:
        large_file_meta = [m for m in prep.file_meta if m["checksum"] not in inline_checksums]
        if large_file_meta:
            call_input = _build_call_input_from_cache(
                call_input, large_file_meta, prep.context_cache_ttl,
            )

    # ── inline parts를 contents에 추가 ──
    if inline_parts:
        contents = call_input.get("contents", "")
        if isinstance(contents, str):
            contents = [contents] if contents else []
        elif isinstance(contents, list):
            contents = [c for c in contents if isinstance(c, str)]
        call_input["contents"] = inline_parts + contents
        call_input.pop("file_checksums", None)

    res = await aio_call(**call_input)
    llm_result, meta = finalize(prep, res, database=body.database)

    return LLMRequestResponse(result=llm_result.model_dump(), meta=meta)


def _build_call_input_from_cache(
    call_input: dict,
    file_meta: list[dict],
    context_cache_ttl: int | None,
) -> dict:
    """Redis 파일 캐시에서 URI를 조회하여 call_input을 구성한다."""
    checksums = [m["checksum"] for m in file_meta]

    # context cache 확인
    hash_key = make_context_cache_key(
        checksums,
        model=call_input.get("model"),
        system_instruction=call_input.get("system"),
    )
    cached = find_context_cache(hash_key)
    if cached:
        call_input["cached_content"] = cached["cached_content_name"]
        contents = call_input.get("contents")
        if isinstance(contents, list):
            call_input["contents"] = [c for c in contents if isinstance(c, str)]
        call_input.pop("file_checksums", None)
        return call_input

    # 개별 파일 URI 조회
    contents_parts: list = []
    for meta in file_meta:
        existing = find_files_api(meta["checksum"])
        if existing:
            if existing.get("split"):
                for uri in existing["uris"]:
                    contents_parts.append(Part.from_uri(uri, mime_type=meta["mime_type"]))
            else:
                contents_parts.append(
                    Part.from_uri(existing["uris"][0], mime_type=meta["mime_type"])
                )
        else:
            raise FileUploadError(
                f"파일 URI를 찾을 수 없습니다: {meta['checksum'][:16]}... "
                "— /v1/files로 먼저 업로드하세요"
            )

    # contents에 Part 추가 (파일 먼저, 텍스트 뒤)
    contents = call_input.get("contents", "")
    if isinstance(contents, str):
        contents = [contents] if contents else []
    elif isinstance(contents, list):
        contents = [c for c in contents if isinstance(c, str)]
    call_input["contents"] = contents_parts + contents
    call_input.pop("file_checksums", None)

    return call_input


@router.post("/v1/files", response_model=FileUploadResponse)
async def handle_file_upload(
    files: list[UploadFile] = File(...),
    checksums: str = Form(...),
    context_cache_ttl: int | None = Form(None),
    model: str | None = Form(None),
    system_instruction: str | None = Form(None),
) -> FileUploadResponse:
    """파일 업로드 (Redis 파일 캐시 miss 시 SDK가 호출).

    multipart/form-data로 파일과 메타정보를 함께 수신한다.
    checksums는 JSON 문자열로 전달: '[{"checksum":"sha256:...","filename":"...","size":123,"mime_type":"..."}]'
    """
    import json

    file_metas = json.loads(checksums)
    if len(files) != len(file_metas):
        raise ValidationError(
            f"파일 수({len(files)})와 checksum 수({len(file_metas)})가 일치하지 않습니다"
        )

    # 임시 파일로 저장 → resolve_files 실행
    temp_paths: list[str] = []
    try:
        for upload_file, meta in zip(files, file_metas):
            suffix = f".{meta['filename'].rsplit('.', 1)[-1]}" if '.' in meta['filename'] else ""
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            content = await upload_file.read()
            tmp.write(content)
            tmp.close()
            temp_paths.append(tmp.name)

        await resolve_files(
            file_metas,
            temp_paths,
            model=model,
            system_instruction=system_instruction,
            context_cache_ttl=context_cache_ttl,
        )
    except Exception as e:
        raise FileUploadError(f"파일 처리 실패: {e}") from e
    finally:
        for p in temp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    return FileUploadResponse(
        uploaded=[m["checksum"] for m in file_metas],
    )


@router.post("/v1/done-check", response_model=DoneCheckResponse)
async def handle_done_check(body: DoneCheckBody) -> DoneCheckResponse:
    """MDB 캐시 존재 여부 확인."""
    llm_input = body.llm_input
    if body.file_checksums:
        llm_input = {**llm_input, "file_checksums": body.file_checksums}

    result = done_check(llm_input, lookup_cache=True, database=body.database)

    return DoneCheckResponse(
        cache_key=result.cache_key,
        canonical_json=result.canonical_json,
        cached=result.cached,
        cache_hit=result.cached is not None,
        collection=result.collection,
    )


@router.post("/v1/fetch", response_model=FetchResponse)
async def handle_fetch(body: FetchBody) -> FetchResponse:
    """캐시 키로 결과 직접 조회."""
    response, meta = fetch_by_cache_key(
        body.cache_key,
        database=body.database,
        as_stream=body.as_stream,
    )

    result_dict = response.model_dump() if hasattr(response, "model_dump") else {"text": "stream"}

    return FetchResponse(result=result_dict, meta=meta)
```

- [ ] **Step 3: 커밋**

```bash
git add packages/llm-request/service/app/routers/
git commit -m "feat(llm-request): service router 엔드포인트 4개"
```

---

## Task 5: Service main.py + pyproject.toml

**Files:**
- Create: `service/app/main.py`
- Create: `service/pyproject.toml`
- Create: `service/Dockerfile`

- [ ] **Step 1: main.py 작성**

llm-handler의 main.py 패턴을 따른다.

```python
"""llm-request Service FastAPI 앱."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import LLMRequestServiceError
from app.core.providers import configure_handlers
from app.models.responses import ErrorResponse
from app.routers import request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("llm_request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 handler SDK 초기화."""
    configure_handlers()
    logger.info("llm-request service 시작")
    yield
    logger.info("llm-request service 종료")


app = FastAPI(
    title="LLM Request Service",
    description="LLM 응답 캐싱 + 파일 처리 서비스",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(request.router, tags=["request"])


@app.exception_handler(LLMRequestServiceError)
async def handle_service_error(_request: Request, exc: LLMRequestServiceError) -> JSONResponse:
    """LLMRequestServiceError를 표준 ErrorResponse로 변환."""
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.message,
            error_type=exc.error_type,
            request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI 요청 검증 에러를 표준 ErrorResponse로 변환."""
    request_id = getattr(_request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="요청 검증 실패",
            detail=str(exc),
            error_type="validation",
            request_id=request_id,
        ).model_dump(),
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """요청별 request_id 생성 + 로깅."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.monotonic()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("미처리 예외: request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="내부 서버 오류",
                error_type="handler",
                request_id=request_id,
            ).model_dump(),
        )

    latency_ms = (time.monotonic() - start_time) * 1000
    logger.info(
        "%s %s → %s (%.0fms) [%s]",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
        request_id,
    )
    return response


@app.get("/health")
async def health_check() -> dict[str, str]:
    """헬스 체크."""
    return {"status": "ok"}
```

- [ ] **Step 2: service/pyproject.toml 작성**

```toml
[project]
name = "kiwoom-llm-request-service"
version = "0.1.0"
description = "LLM Request Service - LLM 응답 캐싱 + 파일 처리 서버"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "pydantic-settings>=2.0.0",
    "python-multipart>=0.0.9",
    "kiwoom-llm-handler>=0.1.0",
    "kiwoom-mdb-handler>=2.0.0",
    "kiwoom-redis-handler>=1.0.0",
    "filetype>=1.2.0",
    "PyMuPDF>=1.24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    "ruff>=0.8.0",
    "pyright>=1.1.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "standard"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Dockerfile 작성**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY service/pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY service/app/ app/

EXPOSE 8101

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
```

- [ ] **Step 4: 커밋**

```bash
git add packages/llm-request/service/app/main.py packages/llm-request/service/pyproject.toml packages/llm-request/service/Dockerfile
git commit -m "feat(llm-request): service main.py + pyproject.toml + Dockerfile"
```

---

## Task 6: SDK exceptions + models

**Files:**
- Create: `sdk/llm_request/exceptions.py`
- Create: `sdk/llm_request/models.py`

- [ ] **Step 1: sdk 디렉토리 생성**

```bash
mkdir -p packages/llm-request/sdk/llm_request
touch packages/llm-request/sdk/llm_request/__init__.py
```

- [ ] **Step 2: exceptions.py 작성**

llm-handler SDK의 exceptions.py 패턴을 따른다.

```python
"""SDK 예외 클래스."""


class SDKError(Exception):
    """llm-request SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.message} ({self.status_code})" if self.status_code else self.message


class ServiceConnectionError(SDKError):
    """llm-request Service 연결 실패."""

    def __init__(self, message: str = "llm-request 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class ServiceError(SDKError):
    """Service가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int, error_type: str | None = None) -> None:
        super().__init__(message, status_code)
        self.error_type = error_type
```

- [ ] **Step 3: models.py 작성**

```python
"""SDK 요청/응답 모델."""

from __future__ import annotations

from typing import Any, TypedDict


class FileChecksum(TypedDict):
    """파일 checksum + 메타정보."""

    checksum: str  # "sha256:<64자 hex>"
    filename: str
    size: int  # bytes
    mime_type: str


class RequestResult(TypedDict):
    """llm_request_async 서버 응답."""

    result: dict[str, Any]
    meta: dict[str, Any]


class DoneCheckResult(TypedDict):
    """done_check 서버 응답."""

    cache_key: str
    canonical_json: str
    cached: dict[str, Any] | None
    cache_hit: bool
    collection: str
```

- [ ] **Step 4: 커밋**

```bash
git add packages/llm-request/sdk/
git commit -m "feat(llm-request): SDK exceptions + models"
```

---

## Task 7: SDK checksum + _client.py

**Files:**
- Copy: `llm_request/checksum.py` → `sdk/llm_request/checksum.py`
- Create: `sdk/llm_request/_client.py`

- [ ] **Step 1: checksum.py를 SDK로 복사**

```bash
cp packages/llm-request/llm_request/checksum.py packages/llm-request/sdk/llm_request/checksum.py
```

checksum.py는 코드 변경 없이 그대로 사용한다 (외부 의존성: filetype만).

- [ ] **Step 2: _client.py 작성**

```python
"""AsyncClient — llm-request Service 통신."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from llm_request.exceptions import ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, FileChecksum, RequestResult


class AsyncClient:
    """llm-request Service와 통신하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        """HTTP 클라이언트 종료."""
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise ServiceConnectionError(f"llm-request 서비스 연결 실패: {e}") from e
        except httpx.TimeoutException as e:
            raise ServiceConnectionError(f"llm-request 서비스 요청 타임아웃: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                error_type = body.get("error_type")
            except Exception:
                error_msg = response.text
                error_type = None
            raise ServiceError(error_msg, status_code=response.status_code, error_type=error_type)

        return response

    async def request(
        self,
        llm_input: dict,
        *,
        file_checksums: list[FileChecksum] | None = None,
        inline_files: list[dict] | None = None,
        cache_mode: bool = True,
        save_mode: bool = True,
        ttl_seconds: int | None = None,
        debug: bool = False,
        database: str | None = None,
        context_cache_ttl: int | None = None,
    ) -> RequestResult:
        """POST /v1/request — LLM 요청."""
        body: dict[str, Any] = {
            "llm_input": llm_input,
            "cache_mode": cache_mode,
            "save_mode": save_mode,
        }
        if file_checksums:
            body["file_checksums"] = file_checksums
        if inline_files:
            body["inline_files"] = inline_files
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if debug:
            body["debug"] = debug
        if database is not None:
            body["database"] = database
        if context_cache_ttl is not None:
            body["context_cache_ttl"] = context_cache_ttl

        resp = await self._request("POST", "/v1/request", json=body)
        return resp.json()

    async def upload_files(
        self,
        file_paths: list[str],
        file_metas: list[FileChecksum],
        *,
        context_cache_ttl: int | None = None,
        model: str | None = None,
        system_instruction: str | None = None,
    ) -> None:
        """POST /v1/files — 파일 업로드 (Redis 파일 캐시 miss 시)."""
        files = []
        opened = []
        for path in file_paths:
            p = Path(path)
            f = open(p, "rb")
            opened.append(f)
            files.append(("files", (p.name, f, "application/octet-stream")))

        data: dict[str, Any] = {"checksums": json.dumps(file_metas)}
        if context_cache_ttl is not None:
            data["context_cache_ttl"] = str(context_cache_ttl)
        if model is not None:
            data["model"] = model
        if system_instruction is not None:
            data["system_instruction"] = system_instruction

        try:
            await self._request("POST", "/v1/files", files=files, data=data)
        finally:
            for f in opened:
                f.close()

    async def done_check(
        self,
        llm_input: dict,
        *,
        file_checksums: list[str] | None = None,
        database: str | None = None,
    ) -> DoneCheckResult:
        """POST /v1/done-check — MDB 캐시 존재 확인."""
        body: dict[str, Any] = {"llm_input": llm_input}
        if file_checksums:
            body["file_checksums"] = file_checksums
        if database is not None:
            body["database"] = database

        resp = await self._request("POST", "/v1/done-check", json=body)
        return resp.json()

    async def fetch(
        self,
        cache_key: str,
        *,
        database: str | None = None,
    ) -> RequestResult:
        """POST /v1/fetch — 캐시 키로 직접 조회."""
        body: dict[str, Any] = {"cache_key": cache_key}
        if database is not None:
            body["database"] = database

        resp = await self._request("POST", "/v1/fetch", json=body)
        return resp.json()
```

- [ ] **Step 3: 커밋**

```bash
git add packages/llm-request/sdk/llm_request/checksum.py packages/llm-request/sdk/llm_request/_client.py
git commit -m "feat(llm-request): SDK checksum + AsyncClient"
```

---

## Task 8: SDK public API (__init__.py)

**Files:**
- Create: `sdk/llm_request/__init__.py`
- Create: `sdk/pyproject.toml`

- [ ] **Step 1: __init__.py 작성**

기존 라이브러리와 동일한 인터페이스를 제공하되, 내부에서는 HTTP 호출.

```python
"""llm-request SDK — LLM 응답 캐싱 서비스 클라이언트.

사용법:
    from llm_request import llm_request_async

    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        file_paths=["report.pdf"],
    )
"""

from __future__ import annotations

import os
from typing import Any

from llm_request._client import AsyncClient
from llm_request.checksum import FileChecksum, file_checksum
from llm_request.exceptions import SDKError, ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, RequestResult

__all__ = [
    "llm_request_async",
    "done_check",
    "fetch_by_cache_key",
    "configure",
    "file_checksum",
    "FileChecksum",
    "SDKError",
    "ServiceConnectionError",
    "ServiceError",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    """서비스 URL을 환경변수에서 읽기."""
    return os.getenv("LLM_REQUEST_URL", "http://localhost:8101")


def _get_client() -> AsyncClient:
    """싱글톤 AsyncClient 반환."""
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


def configure(base_url: str | None = None, timeout: float = 120.0) -> None:
    """SDK 클라이언트를 수동 설정.

    Args:
        base_url: llm-request 서비스 URL (기본: 환경변수 또는 localhost:8101)
        timeout: HTTP 타임아웃 (초)
    """
    global _instance
    url = base_url or _resolve_base_url()
    _instance = AsyncClient(base_url=url, timeout=timeout)


_file_cache_store = None


def _get_file_cache_store():
    """file_cache용 BaseJsonStore 싱글톤 반환. 연결 실패 시 None."""
    global _file_cache_store
    if _file_cache_store is None:
        try:
            from redis_handler import BaseJsonStore
            _file_cache_store = BaseJsonStore(prefix="file_cache")
        except Exception:
            return None
    return _file_cache_store


def _check_redis_file_cache(checksums: list[str]) -> list[str]:
    """Redis 파일 캐시에서 miss인 checksum 목록을 반환한다.

    >20MB 파일의 checksum만 전달해야 한다 (≤20MB는 inline 처리되므로 Redis 불필요).
    Redis 연결이 없거나 실패하면 전부 miss로 처리한다.
    """
    try:
        store = _get_file_cache_store()
        missing = []
        for cs in checksums:
            key = store._make_key("files_api", cs)
            if store._get_json(key) is None:
                missing.append(cs)
        return missing
    except Exception:
        return list(checksums)


async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (result, meta) 튜플 반환.

    내부에서 checksum 계산 + 크기별 분류(inline/upload) + 서버 호출.
    """
    import base64

    client = _get_client()
    file_checksums: list[FileChecksum] | None = None
    inline_files: list[dict] | None = None

    _INLINE_LIMIT = 20 * 1024 * 1024  # 20MB

    if file_paths:
        # 1. checksum 계산 (로컬)
        file_checksums = [file_checksum(p) for p in file_paths]

        # 2. 크기별 분류: ≤20MB → inline, >20MB → upload
        inline_files = []
        large_checksums: list[str] = []

        for i, fc in enumerate(file_checksums):
            if fc["size"] <= _INLINE_LIMIT:
                with open(file_paths[i], "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                inline_files.append({
                    "checksum": fc["checksum"],
                    "filename": fc["filename"],
                    "mime_type": fc["mime_type"],
                    "data_base64": data,
                })
            else:
                large_checksums.append(fc["checksum"])

        # 3. >20MB 파일: Redis 파일 캐시 조회 → miss면 서버로 업로드
        if large_checksums:
            missing = _check_redis_file_cache(large_checksums)
            if missing:
                missing_indices = [
                    i for i, fc in enumerate(file_checksums)
                    if fc["checksum"] in missing and fc["size"] > _INLINE_LIMIT
                ]
                missing_paths = [file_paths[i] for i in missing_indices]
                missing_metas = [file_checksums[i] for i in missing_indices]

                await client.upload_files(
                    missing_paths,
                    missing_metas,
                    context_cache_ttl=context_cache_ttl,
                    model=llm_input.get("model"),
                    system_instruction=llm_input.get("system"),
                )

        if not inline_files:
            inline_files = None

    # 4. 서버에 LLM 요청
    result = await client.request(
        llm_input,
        file_checksums=file_checksums,
        inline_files=inline_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    )

    return result["result"], result["meta"]


async def done_check(
    llm_input: dict,
    *,
    file_checksums: list[str] | None = None,
    database: str | None = None,
) -> DoneCheckResult:
    """MDB 캐시 존재 여부 확인."""
    client = _get_client()
    return await client.done_check(
        llm_input,
        file_checksums=file_checksums,
        database=database,
    )


async def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """캐시 키로 결과 직접 조회."""
    client = _get_client()
    result = await client.fetch(cache_key, database=database)
    return result["result"], result["meta"]
```

- [ ] **Step 2: sdk/pyproject.toml 작성**

```toml
[project]
name = "kiwoom-llm-request"
version = "0.2.0"
description = "LLM Request SDK - LLM 응답 캐싱 서비스 클라이언트"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27.0",
    "filetype>=1.2.0",
    "kiwoom-redis-handler>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.8.0",
    "pyright>=1.1.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["llm_request*"]

[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "standard"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: 커밋**

```bash
git add packages/llm-request/sdk/
git commit -m "feat(llm-request): SDK public API + pyproject.toml"
```

---

## Task 9: Service 테스트

**Files:**
- Create: `tests/service/conftest.py`
- Create: `tests/service/test_router_request.py`
- Move: 기존 테스트 → `tests/service/logic/`

- [ ] **Step 1: 기존 테스트를 service/logic/으로 복사 + import 수정**

```bash
mkdir -p packages/llm-request/tests/service/logic
cp packages/llm-request/tests/test_keying.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_done_check.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_store.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_meta.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_checksum.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_orchestrator.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_resolver.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_cache_store.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_uploader.py packages/llm-request/tests/service/logic/
cp packages/llm-request/tests/test_splitter.py packages/llm-request/tests/service/logic/
```

각 테스트 파일에서 import 경로 변경. 예시 (`test_keying.py`):
```python
# 변경 전
from llm_request.keying import ...

# 변경 후
from app.logic.keying import ...
```

conftest.py도 마찬가지:
```python
# 변경 전
with patch("llm_request.store._get_collection", return_value=col):

# 변경 후
with patch("app.logic.store._get_collection", return_value=col):
```

- [ ] **Step 2: tests/service/conftest.py 작성**

```python
"""Service 테스트 공통 fixture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    """FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture()
def mock_collection():
    """MongoDB 컬렉션을 인메모리 dict 기반으로 모킹."""
    store: dict[str, dict] = {}

    col = MagicMock()

    def _find_one(query: dict):
        _id = query.get("_id")
        return store.get(_id)

    def _replace_one(filter_dict: dict, doc: dict, upsert: bool = False):
        _id = filter_dict.get("_id")
        if _id:
            store[_id] = doc

    col.find_one = MagicMock(side_effect=_find_one)
    col.replace_one = MagicMock(side_effect=_replace_one)

    with patch("app.logic.store._get_collection", return_value=col):
        yield store
```

- [ ] **Step 3: tests/service/test_router_request.py 작성**

```python
"""router 엔드포인트 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def test_health_check(client):
    """헬스 체크 엔드포인트."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_done_check_cache_miss(client, mock_collection):
    """done-check: 캐시 없을 때."""
    resp = client.post("/v1/done-check", json={
        "llm_input": {"contents": "테스트", "model": "gemini-2.5-flash"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cache_hit"] is False
    assert len(data["cache_key"]) == 64


def test_request_cache_hit(client, mock_collection):
    """request: 캐시 히트 시 즉시 반환."""
    # 캐시에 미리 데이터 삽입
    from app.logic.keying import canonicalize_input, make_cache_key

    llm_input = {"contents": "테스트", "model": "gemini-2.5-flash"}
    canonical = canonicalize_input(llm_input)
    cache_key = make_cache_key(canonical)

    mock_collection[cache_key] = {
        "_id": cache_key,
        "result": {
            "text": "캐시된 응답",
            "model": "gemini-2.5-flash",
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            "finish_reason": "STOP",
            "request_id": "req-001",
        },
        "metadata": {"model": "gemini-2.5-flash"},
    }

    resp = client.post("/v1/request", json={
        "llm_input": llm_input,
        "cache_mode": True,
        "save_mode": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["cache_hit"] is True
    assert data["result"]["text"] == "캐시된 응답"
```

- [ ] **Step 4: 테스트 실행**

```bash
cd packages/llm-request/service && pytest tests/service/ -v
```

- [ ] **Step 5: 커밋**

```bash
git add packages/llm-request/tests/service/
git commit -m "test(llm-request): service 테스트 (logic 이동 + router 테스트)"
```

---

## Task 10: SDK 테스트

**Files:**
- Create: `tests/sdk/conftest.py`
- Create: `tests/sdk/test_client.py`

- [ ] **Step 1: tests/sdk/conftest.py 작성**

```python
"""SDK 테스트 공통 fixture."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_sdk():
    """각 테스트 전 SDK 싱글톤 리셋."""
    import llm_request
    llm_request._instance = None
    yield
    llm_request._instance = None
```

- [ ] **Step 2: tests/sdk/test_client.py 작성**

```python
"""SDK client 테스트 (HTTP 호출 mocking)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_request import configure, llm_request_async, done_check, fetch_by_cache_key
from llm_request._client import AsyncClient
from llm_request.checksum import file_checksum


def test_file_checksum(tmp_path):
    """file_checksum: 파일 체크섬 계산."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = file_checksum(str(test_file))
    assert result["checksum"].startswith("sha256:")
    assert result["filename"] == "test.txt"
    assert result["size"] == 11
    assert result["mime_type"] == "text/plain"


@pytest.mark.asyncio
async def test_llm_request_async_no_files():
    """파일 없이 LLM 요청."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {"text": "응답", "model": "gemini-2.5-flash"},
        "meta": {"cache_hit": False, "cache_key": "abc123"},
    }

    with patch.object(AsyncClient, "_request", return_value=mock_response):
        configure("http://test:8101")
        result, meta = await llm_request_async(
            {"contents": "테스트", "model": "gemini-2.5-flash"},
        )
        assert result["text"] == "응답"
        assert meta["cache_hit"] is False


@pytest.mark.asyncio
async def test_done_check():
    """done_check: 캐시 확인."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "cache_key": "abc123",
        "canonical_json": "{}",
        "cached": None,
        "cache_hit": False,
        "collection": "agent_llm",
    }

    with patch.object(AsyncClient, "_request", return_value=mock_response):
        configure("http://test:8101")
        result = await done_check({"contents": "테스트", "model": "gemini-2.5-flash"})
        assert result["cache_hit"] is False


@pytest.mark.asyncio
async def test_fetch_by_cache_key():
    """fetch_by_cache_key: 캐시 키 조회."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {"text": "캐시된 응답"},
        "meta": {"cache_hit": True, "cache_key": "abc123"},
    }

    with patch.object(AsyncClient, "_request", return_value=mock_response):
        configure("http://test:8101")
        result, meta = await fetch_by_cache_key("abc123")
        assert result["text"] == "캐시된 응답"
        assert meta["cache_hit"] is True
```

- [ ] **Step 3: 테스트 실행**

```bash
cd packages/llm-request/sdk && pytest tests/sdk/ -v
```

- [ ] **Step 4: 커밋**

```bash
git add packages/llm-request/tests/sdk/
git commit -m "test(llm-request): SDK 테스트 (checksum, client mocking)"
```

---

## Task 11: workspace pyproject.toml + 정리

**Files:**
- Modify: `packages/llm-request/pyproject.toml` → workspace root로 변환
- 기존 `llm_request/` 디렉토리 정리

- [ ] **Step 1: 루트 pyproject.toml을 workspace로 변환**

```toml
[project]
name = "kiwoom-llm-request-workspace"
version = "0.1.0"
description = "LLM Request workspace (SDK + Service)"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["sdk", "service"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py311"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
```

- [ ] **Step 2: 기존 llm_request/ 디렉토리는 유지 (보관)**

CLAUDE.md 협업 지침에 따라 기존 코드를 삭제하지 않고 보관한다.
나중에 SDK/Service가 검증 완료되면 제거.

- [ ] **Step 3: CLAUDE.md 업데이트**

로드맵 섹션 업데이트:
```markdown
## 로드맵

- **Phase 1 (완료)**: 라이브러리로 file_handler 구현 + 검증
- **Phase 2 (완료)**: file_handler 라이브러리 구현
- **Phase 3 (진행중)**: llm-request 서버화 (SDK+Service 분리)
- **Phase 4**: llm-handler와 합체
```

- [ ] **Step 4: 커밋**

```bash
git add packages/llm-request/pyproject.toml packages/llm-request/CLAUDE.md
git commit -m "feat(llm-request): workspace 구성 + CLAUDE.md 로드맵 업데이트"
```

---

## Task 12: 추가 수정 — runner 리팩토링 + stream 엔드포인트 + SDK 함수 추가

> **배경:** 서버화 과정에서 라우터가 `prepare`/`finalize`를 직접 조합하면서 runner.py의 4개 public 함수가 dead code가 됨. 파일 처리 로직(inline base64, Redis URI 조회)이 라우터에 들어가면서 라우터가 비즈니스 로직을 갖게 됨. stream 엔드포인트 추가 시 같은 로직을 또 써야 하는 문제.
>
> **방향:** 라우터는 HTTP 변환만 하는 얇은 래퍼로, runner.py가 전체 비즈니스 흐름을 담당하도록 복원. runner.py의 4개 함수를 서버 모드 입력(inline_files, file_meta)에 맞게 재설계.

### 변경 목표

**Service runner.py — 4개 함수 서버 모드로 재설계:**

| 함수 | 역할 |
|------|------|
| `llm_request_async` | async 단건 요청 (전체 흐름) |
| `llm_request` | sync 래퍼 |
| `llm_request_stream_async` | async 스트리밍 (전체 흐름) |
| `llm_request_stream` | sync 래퍼 |

**Service 라우터 — HTTP 변환만:**

| 엔드포인트 | 라우터 역할 |
|-----------|-----------|
| `POST /v1/request` | body 파싱 → `llm_request_async()` 호출 → 응답 포맷팅 |
| `POST /v1/request/stream` | body 파싱 → `llm_request_stream_async()` 호출 → SSE 변환 |

**SDK — 4개 함수 (동일 이름, HTTP 클라이언트):**

| 함수 | 호출 엔드포인트 |
|------|---------------|
| `llm_request_async` | `/v1/request` |
| `llm_request` | `/v1/request` (sync 래퍼) |
| `llm_request_stream_async` | `/v1/request/stream` |
| `llm_request_stream` | `/v1/request/stream` (sync 래퍼) |

### Step 1: runner.py — 파일 처리 함수 추가 + 4개 함수 재설계

라우터에 있던 파일 처리 로직을 runner.py로 이동. 두 개의 독립 함수로 분리:

```python
# ── 파일 처리 헬퍼 (라우터에서 이동) ─────────────────────────

def _apply_inline_files(
    call_input: dict,
    inline_files: list[dict] | None,
) -> tuple[dict, set[str]]:
    """≤20MB inline 파일을 base64 디코딩하여 call_input에 추가.

    Returns:
        (수정된 call_input, inline으로 처리된 checksum set)
    """
    if not inline_files:
        return call_input, set()

    import base64

    inline_parts: list = []
    inline_checksums: set[str] = set()

    for f in inline_files:
        raw = base64.b64decode(f["data_base64"])
        inline_parts.append(Part.from_bytes(data=raw, mime_type=f["mime_type"]))
        inline_checksums.add(f["checksum"])

    if inline_parts:
        contents = call_input.get("contents", "")
        if isinstance(contents, str):
            contents = [contents] if contents else []
        elif isinstance(contents, list):
            contents = [c for c in contents if isinstance(c, str)]
        call_input["contents"] = inline_parts + contents
        call_input.pop("file_checksums", None)

    return call_input, inline_checksums


def _apply_cached_files(
    call_input: dict,
    file_meta: list[dict],
    context_cache_ttl: int | None,
) -> dict:
    """Redis 파일 캐시에서 URI를 조회하여 call_input에 추가.

    >20MB 파일의 URI를 Redis에서 가져온다 (SDK가 /v1/files로 사전 업로드).
    """
    from app.logic.file_handler.cache_store import (
        find_context_cache,
        find_files_api,
        make_context_cache_key,
    )
    from app.core.errors import FileUploadError

    checksums = [m["checksum"] for m in file_meta]

    # context cache 확인
    hash_key = make_context_cache_key(
        checksums,
        model=call_input.get("model"),
        system_instruction=call_input.get("system"),
    )
    cached = find_context_cache(hash_key)
    if cached:
        call_input["cached_content"] = cached["cached_content_name"]
        contents = call_input.get("contents")
        if isinstance(contents, list):
            call_input["contents"] = [c for c in contents if isinstance(c, str)]
        call_input.pop("file_checksums", None)
        return call_input

    # 개별 파일 URI 조회
    contents_parts: list = []
    for meta in file_meta:
        existing = find_files_api(meta["checksum"])
        if existing:
            if existing.get("split"):
                for uri in existing["uris"]:
                    contents_parts.append(Part.from_uri(uri, mime_type=meta["mime_type"]))
            else:
                contents_parts.append(
                    Part.from_uri(existing["uris"][0], mime_type=meta["mime_type"])
                )
        else:
            raise FileUploadError(
                f"파일 URI를 찾을 수 없습니다: {meta['checksum'][:16]}... "
                "— /v1/files로 먼저 업로드하세요"
            )

    contents = call_input.get("contents", "")
    if isinstance(contents, str):
        contents = [contents] if contents else []
    elif isinstance(contents, list):
        contents = [c for c in contents if isinstance(c, str)]
    call_input["contents"] = contents_parts + contents
    call_input.pop("file_checksums", None)

    return call_input


# ── 공통 call_input 조립 ─────────────────────────────────────

def _build_server_call_input(
    prep: _Prepared,
    inline_files: list[dict] | None,
) -> dict:
    """inline 파일 + Redis 캐시 파일을 조합하여 최종 call_input을 만든다."""
    call_input = dict(prep.llm_input)

    # 1. inline 파일 적용
    call_input, inline_checksums = _apply_inline_files(call_input, inline_files)

    # 2. >20MB 파일: Redis에서 URI 조회
    if prep.file_meta:
        large_file_meta = [
            m for m in prep.file_meta
            if m["checksum"] not in inline_checksums
        ]
        if large_file_meta:
            call_input = _apply_cached_files(
                call_input, large_file_meta, prep.context_cache_ttl,
            )

    return call_input
```

4개 public 함수 재설계:

```python
# ── Public API ─────────────────────────────────────────────────

async def llm_request_async(
    llm_input: dict,
    *,
    file_meta: list[dict] | None = None,
    inline_files: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (async). (LLMResponse, meta) 튜플 반환."""
    prep = prepare(
        llm_input,
        file_meta=file_meta,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )
    if prep.cached_output:
        return prep.cached_output

    call_input = _build_server_call_input(prep, inline_files)
    res = await aio_call(**call_input)
    return finalize(prep, res, database=database)


def llm_request(
    llm_input: dict,
    *,
    file_meta: list[dict] | None = None,
    inline_files: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """단일 LLM 요청 실행 (sync)."""
    import asyncio
    return asyncio.run(llm_request_async(
        llm_input,
        file_meta=file_meta, inline_files=inline_files,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    file_meta: list[dict] | None = None,
    inline_files: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> AsyncIterator[StreamChunk]:
    """스트리밍 LLM 요청 (async). StreamChunk를 yield.

    캐시 히트 시 단일 StreamChunk 1개 yield.
    스트림 완료 후 MDB에 결과 자동 저장.
    """
    prep = prepare(
        llm_input,
        file_meta=file_meta,
        cache_mode=cache_mode, save_mode=save_mode,
        ttl_seconds=ttl_seconds, debug=debug, database=database,
        context_cache_ttl=context_cache_ttl,
    )

    if prep.cached_output:
        response, meta = prep.cached_output
        yield _to_stream_chunk(response)
        return

    call_input = _build_server_call_input(prep, inline_files)

    accumulated: list[StreamChunk] = []
    async for chunk in aio_stream(**call_input):
        accumulated.append(chunk)
        yield chunk
    _finalize_stream(prep, accumulated, database=database)


def llm_request_stream(
    llm_input: dict,
    *,
    file_meta: list[dict] | None = None,
    inline_files: list[dict] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> Iterator[StreamChunk]:
    """스트리밍 LLM 요청 (sync)."""
    import asyncio

    async def _collect():
        results = []
        async for chunk in llm_request_stream_async(
            llm_input,
            file_meta=file_meta, inline_files=inline_files,
            cache_mode=cache_mode, save_mode=save_mode,
            ttl_seconds=ttl_seconds, debug=debug, database=database,
            context_cache_ttl=context_cache_ttl,
        ):
            results.append(chunk)
        return results

    return iter(asyncio.run(_collect()))
```

기존 `build_call_input` (ResolvedFiles 기반)은 제거. `_build_call_input_from_cache`도 라우터에서 제거 (runner.py의 `_apply_cached_files`로 대체).

### Step 2: 라우터 — HTTP 변환만 (얇은 래퍼로 리팩토링)

`service/app/routers/request.py`를 대폭 간소화:

```python
"""LLM Request 엔드포인트."""

from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from app.core.errors import FileUploadError, ValidationError
from app.logic.done_check import done_check
from app.logic.file_handler import resolve_files
from app.logic.runner import llm_request_async, llm_request_stream_async
from app.logic.store import fetch_by_cache_key
from app.models.requests import DoneCheckBody, FetchBody, LLMRequestBody
from app.models.responses import (
    DoneCheckResponse,
    FetchResponse,
    FileUploadResponse,
    LLMRequestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── /v1/request ──────────────────────────────────────────────

@router.post("/v1/request", response_model=LLMRequestResponse)
async def handle_request(body: LLMRequestBody) -> LLMRequestResponse:
    """LLM 요청 (MDB 캐시 조회 → 호출 → 저장)."""
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    inline_files = None
    if body.inline_files:
        inline_files = [f.model_dump() for f in body.inline_files]

    llm_result, meta = await llm_request_async(
        body.llm_input,
        file_meta=file_meta,
        inline_files=inline_files,
        cache_mode=body.cache_mode,
        save_mode=body.save_mode,
        ttl_seconds=body.ttl_seconds,
        debug=body.debug,
        database=body.database,
        context_cache_ttl=body.context_cache_ttl,
    )

    return LLMRequestResponse(result=llm_result.model_dump(), meta=meta)


# ── /v1/request/stream ──────────────────────────────────────

@router.post("/v1/request/stream")
async def handle_request_stream(body: LLMRequestBody) -> StreamingResponse:
    """스트리밍 LLM 요청 (SSE)."""
    file_meta = None
    if body.file_checksums:
        file_meta = [fc.model_dump() for fc in body.file_checksums]

    inline_files = None
    if body.inline_files:
        inline_files = [f.model_dump() for f in body.inline_files]

    async def _sse():
        async for chunk in llm_request_stream_async(
            body.llm_input,
            file_meta=file_meta,
            inline_files=inline_files,
            cache_mode=body.cache_mode,
            save_mode=body.save_mode,
            ttl_seconds=body.ttl_seconds,
            debug=body.debug,
            database=body.database,
            context_cache_ttl=body.context_cache_ttl,
        ):
            event = {
                "type": "chunk",
                "text": chunk.text,
                "finish_reason": chunk.finish_reason,
            }
            if chunk.usage:
                event["usage"] = chunk.usage.model_dump()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


# ── /v1/files (변경 없음) ────────────────────────────────────
# handle_file_upload은 기존 코드 유지

# ── /v1/done-check (변경 없음) ───────────────────────────────
# handle_done_check은 기존 코드 유지

# ── /v1/fetch (변경 없음) ────────────────────────────────────
# handle_fetch은 기존 코드 유지
```

### Step 3: SDK — 나머지 3개 함수 추가

`sdk/llm_request/__init__.py`에 추가:

```python
import asyncio


def llm_request(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """단일 LLM 요청 실행 (sync). llm_request_async의 동기 래퍼."""
    return asyncio.run(llm_request_async(
        llm_input,
        file_paths=file_paths,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    ))


async def llm_request_stream_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (async). SSE 이벤트를 파싱하여 dict로 yield.

    Returns:
        AsyncIterator of event dicts:
        - {"type": "chunk", "text": "...", "finish_reason": None}
        - {"type": "done", "usage": {...}}
    """
    import base64

    client = _get_client()
    file_checksums, inline_files = _prepare_files(file_paths)

    if file_paths and file_checksums:
        await _upload_missing_files(client, file_paths, file_checksums, inline_files,
                                     context_cache_ttl, llm_input)

    async for event in client.request_stream(
        llm_input,
        file_checksums=file_checksums,
        inline_files=inline_files,
        cache_mode=cache_mode,
        save_mode=save_mode,
        ttl_seconds=ttl_seconds,
        debug=debug,
        database=database,
        context_cache_ttl=context_cache_ttl,
    ):
        yield event


def llm_request_stream(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    ttl_seconds: int | None = None,
    debug: bool = False,
    database: str | None = None,
    context_cache_ttl: int | None = None,
) -> Iterator[dict[str, Any]]:
    """스트리밍 LLM 요청 (sync). llm_request_stream_async의 동기 래퍼.

    Note: 내부적으로 새 event loop을 생성하여 실행. 기존 event loop 안에서는 사용 불가.
    """
    import asyncio

    async def _collect():
        results = []
        async for event in llm_request_stream_async(
            llm_input,
            file_paths=file_paths,
            cache_mode=cache_mode,
            save_mode=save_mode,
            ttl_seconds=ttl_seconds,
            debug=debug,
            database=database,
            context_cache_ttl=context_cache_ttl,
        ):
            results.append(event)
        return results

    return iter(asyncio.run(_collect()))
```

참고: SDK의 `llm_request_async`와 `llm_request_stream_async`에서 파일 처리 로직(checksum 계산 + inline/large 분류 + upload)이 중복되므로, 공통 헬퍼로 추출 가능:

```python
_INLINE_LIMIT = 20 * 1024 * 1024  # 20MB


def _prepare_files(
    file_paths: list[str] | None,
) -> tuple[list[FileChecksum] | None, list[dict] | None]:
    """파일 경로에서 checksum 계산 + inline/large 분류."""
    if not file_paths:
        return None, None

    import base64

    file_checksums = [file_checksum(p) for p in file_paths]
    inline_files: list[dict] = []

    for i, fc in enumerate(file_checksums):
        if fc["size"] <= _INLINE_LIMIT:
            with open(file_paths[i], "rb") as f:
                data = base64.b64encode(f.read()).decode()
            inline_files.append({
                "checksum": fc["checksum"],
                "filename": fc["filename"],
                "mime_type": fc["mime_type"],
                "data_base64": data,
            })

    return file_checksums, inline_files or None


async def _upload_missing_files(
    client: AsyncClient,
    file_paths: list[str],
    file_checksums: list[FileChecksum],
    inline_files: list[dict] | None,
    context_cache_ttl: int | None,
    llm_input: dict,
) -> None:
    """>20MB 파일 중 Redis에 없는 것만 서버로 업로드."""
    inline_checksums = {f["checksum"] for f in inline_files} if inline_files else set()
    large_checksums = [
        fc["checksum"] for fc in file_checksums
        if fc["size"] > _INLINE_LIMIT and fc["checksum"] not in inline_checksums
    ]

    if not large_checksums:
        return

    missing = _check_redis_file_cache(large_checksums)
    if not missing:
        return

    missing_indices = [
        i for i, fc in enumerate(file_checksums)
        if fc["checksum"] in missing and fc["size"] > _INLINE_LIMIT
    ]
    await client.upload_files(
        [file_paths[i] for i in missing_indices],
        [file_checksums[i] for i in missing_indices],
        context_cache_ttl=context_cache_ttl,
        model=llm_input.get("model"),
        system_instruction=llm_input.get("system"),
    )
```

### Step 4: SDK `_client.py` — `request_stream` 메서드 추가

```python
    async def request_stream(
        self,
        llm_input: dict,
        *,
        file_checksums: list[FileChecksum] | None = None,
        inline_files: list[dict] | None = None,
        cache_mode: bool = True,
        save_mode: bool = True,
        ttl_seconds: int | None = None,
        debug: bool = False,
        database: str | None = None,
        context_cache_ttl: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """POST /v1/request/stream — SSE 스트리밍 요청."""
        import json

        body: dict[str, Any] = {
            "llm_input": llm_input,
            "cache_mode": cache_mode,
            "save_mode": save_mode,
        }
        if file_checksums:
            body["file_checksums"] = file_checksums
        if inline_files:
            body["inline_files"] = inline_files
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if debug:
            body["debug"] = debug
        if database is not None:
            body["database"] = database
        if context_cache_ttl is not None:
            body["context_cache_ttl"] = context_cache_ttl

        async with self._http.stream("POST", "/v1/request/stream", json=body) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                try:
                    err_body = resp.json()
                    raise ServiceError(err_body.get("error", resp.text), resp.status_code)
                except Exception:
                    raise ServiceError(resp.text, resp.status_code)

            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    yield json.loads(data)
```

### Step 5: `__all__` 업데이트

`sdk/llm_request/__init__.py`의 `__all__`에 추가:
```python
__all__ = [
    "llm_request_async",
    "llm_request",
    "llm_request_stream_async",
    "llm_request_stream",
    "done_check",
    "fetch_by_cache_key",
    "configure",
    "file_checksum",
    "FileChecksum",
    "SDKError",
    "ServiceConnectionError",
    "ServiceError",
]
```

### Step 6: 테스트

- [ ] runner.py 리팩토링 후 기존 테스트 통과 확인
- [ ] stream 엔드포인트 테스트 (`tests/service/test_router_request.py`)
- [ ] SDK 4개 함수 테스트 (`tests/sdk/test_client.py`)
- [ ] 라우터 간소화 후 기존 라우터 테스트 통과 확인

### Step 7: 커밋

```bash
git add packages/llm-request/service/app/logic/runner.py
git add packages/llm-request/service/app/routers/request.py
git add packages/llm-request/sdk/llm_request/
git commit -m "feat(llm-request): runner 리팩토링 + stream 엔드포인트 + SDK 4함수 완성"
```
