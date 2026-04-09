# llm-request Package Architecture

## Directory Structure

```
packages/llm-request/
├── sdk/                          # SDK (agent 머신에서 실행)
│   ├── llm_request/
│   │   ├── __init__.py           # Public API
│   │   ├── _client.py            # httpx AsyncClient
│   │   ├── checksum.py           # 로컬 파일 SHA-256
│   │   ├── exceptions.py         # SDK 예외
│   │   └── models.py             # 요청/응답 TypedDict
│   └── tests/
│       ├── conftest.py           # SDK 테스트 픽스처
│       └── test_client.py        # SDK 테스트 (8개)
│
├── service/                      # Service (별도 서버, FastAPI)
│   ├── app/
│   │   ├── main.py               # FastAPI 앱 진입점
│   │   ├── config.py             # 환경변수 (port=8101)
│   │   ├── core/
│   │   │   ├── errors.py         # 에러 계층 구조
│   │   │   └── providers.py      # MDB/Redis/LLM 연결 초기화
│   │   ├── models/
│   │   │   ├── requests.py       # 요청 DTO
│   │   │   └── responses.py      # 응답 DTO
│   │   ├── routers/
│   │   │   └── request.py        # 5개 엔드포인트
│   │   └── logic/
│   │       ├── types.py          # FileChecksum TypedDict
│   │       ├── runner.py         # 핵심 LLM 호출
│   │       ├── keying.py         # 캐시 키 생성
│   │       ├── done_check.py     # 캐시 조회
│   │       ├── store.py          # MDB 저장/조회
│   │       ├── meta.py           # 메타데이터 추출
│   │       ├── orchestrator.py   # 배치 병렬 처리
│   │       └── file_handler/
│   │           ├── resolver.py   # 파일 크기별 분기
│   │           ├── cache_store.py # Redis CRUD
│   │           ├── uploader.py   # Files API 업로드
│   │           └── splitter.py   # PDF 분할
│   └── tests/
│       ├── conftest.py           # Service 테스트 픽스처
│       ├── test_router_request.py # 엔드포인트 테스트 (6개)
│       └── logic/
│           ├── test_keying.py        # 캐시 키 테스트 (28개)
│           ├── test_done_check.py    # 캐시 조회 테스트 (6개)
│           ├── test_store.py         # MDB 저장 테스트 (8개)
│           ├── test_meta.py          # 메타데이터 테스트 (3개)
│           ├── test_orchestrator.py  # 배치 처리 테스트 (6개)
│           ├── test_resolver.py      # 파일 분기 테스트 (6개)
│           ├── test_cache_store.py   # Redis CRUD 테스트 (12개)
│           ├── test_uploader.py      # 업로드 테스트 (6개)
│           └── test_splitter.py      # PDF 분할 테스트 (7개)
│
├── docs/                         # 설계 문서
├── quickstarts/                  # 예제 노트북 + 테스트 PDF
├── pyproject.toml
└── requirements.txt
```

## Architecture Overview

```
          SDK (5파일)              Service (15파일)
        ┌──────────────┐        ┌─────────────────────┐
  사용자 │ checksum 계산  │  HTTP  │ MDB 캐시 조회/저장    │
  코드  │ HTTP 요청      │──────→│ 파일 처리 (Files API) │
   ↓    │ 응답 변환      │       │ LLM 호출 (Gemini)   │
  SDK   │              │←──────│ 결과 반환            │
        └──────────────┘        └─────────────────────┘
```

---

## Summary

**Service (15파일)**

- `config.py` — port 8101, 환경변수 관리
- `core/` — errors.py, providers.py (MDB/Redis/LLM 연결)
- `models/` — 요청/응답 DTO
- `routers/request.py` — 5개 엔드포인트
- `logic/` — 기존 비즈니스 로직 이동 (import 경로 + file_meta 파라미터 수정)

**SDK (5파일)**

- `checksum.py` — 로컬 파일 SHA-256
- `_client.py` — httpx AsyncClient
- `__init__.py` — `llm_request_async`, `done_check`, `fetch_by_cache_key`
- `exceptions.py`, `models.py`

**테스트 (14파일)**

- `service/tests/logic/` — 기존 10개 테스트 import 경로 변경
- `service/tests/test_router_request.py` — 엔드포인트 6개 테스트
- `sdk/tests/test_client.py` — checksum + HTTP mocking 8개 테스트

### Migration Map

```
기존 라이브러리 (llm_request/)          →  어디로 갔는가
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

llm_request/
├── checksum.py ─────────────────→  📦 sdk/llm_request/checksum.py
│                                    (로컬 파일 접근 필요 → SDK에 남음)
│
├── runner.py ───────────────────→  🔧 service/app/logic/runner.py
│                                    (file_meta 파라미터 추가, _resolve_file_meta 제거)
│
├── keying.py ───────────────────→  📋 service/app/logic/keying.py (그대로)
├── done_check.py ───────────────→  📋 service/app/logic/done_check.py (그대로)
├── store.py ────────────────────→  📋 service/app/logic/store.py (그대로)
├── meta.py ─────────────────────→  📋 service/app/logic/meta.py (그대로)
├── orchestrator.py ─────────────→  📋 service/app/logic/orchestrator.py (그대로)
│
├── file_handler/
│   ├── resolver.py ─────────────→  📋 service/app/logic/file_handler/resolver.py
│   ├── cache_store.py ──────────→  📋 service/app/logic/file_handler/cache_store.py
│   ├── uploader.py ─────────────→  📋 service/app/logic/file_handler/uploader.py
│   └── splitter.py ─────────────→  📋 service/app/logic/file_handler/splitter.py
│
└── __init__.py ─────────────────→  ✨ sdk/llm_request/__init__.py (새로 작성)
                                     (동일 인터페이스, 내부는 HTTP 호출)
```

### 새로 생성된 파일

```
SDK (agent 머신에서 실행)
├── __init__.py ·············· public API (llm_request_async 등)
├── _client.py ··············· httpx로 서버에 HTTP 요청
├── exceptions.py ············ SDK 예외 (ServiceError 등)
└── models.py ················ 요청/응답 TypedDict

Service (별도 서버)
├── main.py ·················· FastAPI 앱 진입점
├── config.py ················ 환경변수 (port=8101)
├── core/
│   ├── errors.py ············ HTTP 에러 → JSON 변환
│   └── providers.py ········· MDB/Redis/LLM 연결 초기화
├── models/
│   ├── requests.py ·········· 요청 DTO (LLMRequestBody 등)
│   └── responses.py ········· 응답 DTO (LLMRequestResponse 등)
├── routers/
│   └── request.py ··········· 5개 엔드포인트
└── logic/
    └── types.py ············· FileChecksum TypedDict
```

---

## SDK

### `__init__.py` — Public API

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `llm_request(llm_input, *, file_paths, cache_mode, save_mode, ttl_seconds, debug, database, context_cache_ttl) -> tuple[dict, dict]` | 동기 단건 LLM 호출 (asyncio.run 래퍼) |
| `def` | `llm_request_async(…) -> tuple[dict, dict]` | 비동기 단건 LLM 호출 |
| `def` | `llm_request_stream(…) -> Iterator[dict]` | 동기 스트리밍 LLM 호출 |
| `def` | `llm_request_stream_async(…) -> AsyncIterator[dict]` | 비동기 스트리밍 LLM 호출 (SSE) |
| `def` | `done_check(llm_input, *, file_checksums, database) -> DoneCheckResult` | MDB 캐시 존재 여부 확인 |
| `def` | `fetch_by_cache_key(cache_key, *, database) -> tuple[dict, dict]` | cache_key로 캐시 결과 직접 조회 |
| `def` | `configure(base_url, timeout) -> None` | SDK 클라이언트 수동 설정 |
| `def` | `file_checksum(source, *, mime_type, filename) -> FileChecksum` | 로컬 파일 SHA-256 + 메타 추출 |
| const | `_INLINE_LIMIT` | 20MB (인라인 vs 업로드 분기 기준) |

### `_client.py` — HTTP 클라이언트

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `AsyncClient` | httpx 기반 비동기 HTTP 클라이언트 |
| `method` | `request(llm_input, *, file_checksums, inline_files, …) -> RequestResult` | POST /v1/request |
| `method` | `request_stream(…) -> AsyncIterator[dict]` | POST /v1/request/stream (SSE) |
| `method` | `upload_files(file_paths, file_metas, …) -> None` | POST /v1/files (>20MB 파일 업로드) |
| `method` | `done_check(llm_input, *, file_checksums, database) -> DoneCheckResult` | POST /v1/done-check |
| `method` | `fetch(cache_key, *, database) -> RequestResult` | POST /v1/fetch |
| `method` | `close() -> None` | HTTP 클라이언트 종료 |

### `checksum.py` — 파일 체크섬

| 종류 | 이름 | 설명 |
|------|------|------|
| `TypedDict` | `FileChecksum` | checksum, filename, size, mime_type |
| `def` | `file_checksum(source, *, mime_type, filename) -> FileChecksum` | SHA-256 + MIME 감지 + 크기 반환 |

### `exceptions.py` — SDK 예외

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `SDKError(Exception)` | 기본 SDK 예외 (message, status_code) |
| `class` | `ServiceConnectionError(SDKError)` | 서비스 연결/타임아웃 실패 |
| `class` | `ServiceError(SDKError)` | 서비스 에러 응답 (error_type: validation\|cache\|file\|handler) |

### `models.py` — 타입 정의

| 종류 | 이름 | 설명 |
|------|------|------|
| `TypedDict` | `RequestResult` | result (dict), meta (dict) |
| `TypedDict` | `DoneCheckResult` | cache_key, canonical_json, cached, cache_hit, collection |

---

## Service

### `main.py` — FastAPI 진입점

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `lifespan(app) -> AsyncContextManager` | 앱 시작 시 handler SDK 초기화 |
| `def` | `handle_service_error(request, exc) -> JSONResponse` | LLMRequestServiceError 핸들러 |
| `def` | `handle_validation_error(request, exc) -> JSONResponse` | FastAPI ValidationError 핸들러 |
| `def` | `request_logging_middleware(request, call_next)` | request_id 생성 + 로깅 미들웨어 |
| `def` | `health_check() -> dict` | GET /health → {"status": "ok"} |

### `config.py` — 환경변수

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `Settings(BaseSettings)` | mdb_handler_url, llm_handler_url, redis_url, mongodb_database, host, port |
| const | `settings` | 전역 Settings 인스턴스 |

### `core/errors.py` — 에러 계층

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `LLMRequestServiceError(Exception)` | 기본 에러 (message, status_code=500, error_type="handler") |
| `class` | `CacheError(…)` | 캐시 조회/저장 실패 (status_code=502) |
| `class` | `FileUploadError(…)` | 파일 업로드 처리 실패 (status_code=502) |
| `class` | `ValidationError(…)` | 요청 유효성 검증 실패 (status_code=422) |

### `core/providers.py` — 외부 서비스 연결

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `configure_handlers() -> None` | MDB/Redis/LLM handler SDK 초기화 (lifespan에서 호출) |

### `models/requests.py` — 요청 DTO

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `FileChecksumDTO(BaseModel)` | checksum, filename, size, mime_type |
| `class` | `InlineFileDTO(BaseModel)` | checksum, filename, mime_type, data_base64 (≤20MB) |
| `class` | `LLMRequestBody(BaseModel)` | POST /v1/request 요청 본문 |
| `class` | `DoneCheckBody(BaseModel)` | POST /v1/done-check 요청 본문 |
| `class` | `FetchBody(BaseModel)` | POST /v1/fetch 요청 본문 |

### `models/responses.py` — 응답 DTO

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `LLMRequestResponse(BaseModel)` | result (dict), meta (dict) |
| `class` | `DoneCheckResponse(BaseModel)` | cache_key, canonical_json, cached, cache_hit, collection |
| `class` | `FetchResponse(BaseModel)` | result (dict), meta (dict) |
| `class` | `FileUploadResponse(BaseModel)` | uploaded (list), message |
| `class` | `ErrorResponse(BaseModel)` | error, detail, error_type, request_id |

### `routers/request.py` — API 엔드포인트

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `handle_request(body) -> LLMRequestResponse` | POST /v1/request — 단건 LLM 호출 |
| `def` | `handle_request_stream(body) -> StreamingResponse` | POST /v1/request/stream — SSE 스트리밍 |
| `def` | `handle_file_upload(files, checksums, …) -> FileUploadResponse` | POST /v1/files — 멀티파트 파일 업로드 |
| `def` | `handle_done_check(body) -> DoneCheckResponse` | POST /v1/done-check — 캐시 존재 확인 |
| `def` | `handle_fetch(body) -> FetchResponse` | POST /v1/fetch — cache_key로 직접 조회 |

### `logic/types.py` — 내부 타입

| 종류 | 이름 | 설명 |
|------|------|------|
| `TypedDict` | `FileChecksum` | checksum, filename, size, mime_type |

---

## Service — Logic Layer

### `logic/runner.py` — 핵심 LLM 호출

| 종류 | 이름 | 설명 |
|------|------|------|
| `dataclass` | `_Prepared` | prepare 결과 상태 홀더 (캐시 키, 메타, 캐시 히트 등) |
| `def` | `prepare(llm_input, …) -> _Prepared` | 캐시 조회 + 메타 준비 → 캐시 히트/미스/에러 판단 |
| `def` | `finalize(prep, llm_result, …) -> tuple[LLMResponse, dict]` | LLM 결과 MDB 저장 + (response, meta) 반환 |
| `def` | `llm_request(llm_input, …) -> tuple[LLMResponse, dict]` | 동기 단건 LLM 호출 |
| `def` | `llm_request_async(llm_input, …) -> tuple[LLMResponse, dict]` | 비동기 단건 LLM 호출 |
| `def` | `llm_request_stream(llm_input, …) -> Iterator[StreamChunk]` | 동기 스트리밍 LLM 호출 |
| `def` | `llm_request_stream_async(llm_input, …) -> AsyncIterator[StreamChunk]` | 비동기 스트리밍 LLM 호출 |
| `_def` | `_build_live_meta(prep) -> dict` | 라이브 호출용 meta dict 구성 |
| `_def` | `_finalize_stream(prep, chunks, …) -> None` | 스트림 청크 조립 → MDB 저장 |
| `_def` | `_to_stream_chunk(response) -> StreamChunk` | LLMResponse → StreamChunk 변환 (캐시 히트 스트리밍용) |
| `_def` | `_apply_inline_files(call_input, inline_files) -> tuple[dict, set]` | base64 인라인 파일 디코딩 → Part 주입 |
| `_def` | `_apply_cached_files(call_input, file_meta, …) -> dict` | Redis에서 파일 URI 조회 → call_input에 주입 |
| `_def` | `_build_server_call_input(prep, inline_files) -> dict` | 인라인 + 캐시 파일 합쳐 최종 call_input 생성 |

### `logic/keying.py` — 캐시 키 생성

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `build_llm_input_subset(llm_input, fields) -> dict` | llm_input에서 캐시 대상 필드 추출 (None → "") |
| `def` | `canonicalize_llm_input(llm_input, …) -> str` | 재귀 정규화 → 결정적 JSON (키 정렬, float 반올림, volatile 키 제거, FileRef/Part 축소) |
| `def` | `canonicalize_input(llm_input, **kwargs) -> str` | subset + canonicalize 한번에 (외부 진입점) |
| `def` | `make_cache_key(canonical_json, *, debug) -> str` | canonical JSON → SHA-256 hex (64자) |
| const | `_LLM_INPUT_FIELDS` | 캐시 대상 필드 튜플 |
| const | `DEFAULT_VOLATILE_KEYS` | 제외 키 frozenset (timestamp, ts, created_at 등) |

### `logic/done_check.py` — 캐시 조회

| 종류 | 이름 | 설명 |
|------|------|------|
| `dataclass` | `DoneCheckResult` | collection, cache_key, canonical_json, cached, cache_error (frozen) |
| `def` | `done_check(llm_input, *, lookup_cache, database) -> DoneCheckResult` | 정규화 → 해시 키 생성 → MDB 조회 (선택적) |
| const | `DEFAULT_COLLECTION` | "agent_llm" |

### `logic/store.py` — MDB 저장/조회

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `cache_get(cache_key, collection, database) -> dict \| None` | cache_key로 MDB 문서 조회 |
| `def` | `cache_set(doc, collection, database) -> None` | MDB에 문서 upsert |
| `def` | `save_result(cache_key, canonical_json, llm_result, meta, collection, *, database, ttl_seconds, requested_at, file_meta) -> None` | LLM 결과 → MDB 문서 구성 + 저장 |
| `def` | `fetch_by_cache_key(cache_key, *, database, as_stream) -> tuple` | cache_key로 캐시 결과 조회 (LLMResponse 또는 StreamChunk) |
| `_def` | `_get_collection(collection, database)` | MDB 컬렉션 객체 획득 |
| const | `DEFAULT_HISTORY_DB` | "kw_ai" |

### `logic/meta.py` — 메타데이터

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `build_meta(llm_input) -> dict` | llm_input에서 service_name, model 추출 |

### `logic/orchestrator.py` — 배치 처리

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `run_many(states, *, concurrency, database) -> AsyncIterator[tuple]` | N건 병렬 비동기 호출, 완료순 yield (_index, _state 포함) |
| `def` | `run_many_stream(states, *, concurrency, database) -> AsyncIterator[dict]` | N건 병렬 스트리밍, 이벤트 큐 yield (_type: chunk/done/cache_hit/error) |

---

## Service — File Handler

### `file_handler/resolver.py` — 파일 처리 분기

| 종류 | 이름 | 설명 |
|------|------|------|
| `dataclass` | `ResolvedFiles` | contents_parts (list[Part]), cached_content (optional name) |
| `def` | `resolve_files(file_metas, file_paths, *, model, system_instruction, context_cache_ttl) -> ResolvedFiles` | Redis context cache 조회 → 히트면 cached_content 반환; 미스면 파일 크기별 분기 처리 |
| `_def` | `_resolve_split_pdf(meta, path) -> tuple[list[Part], list[FileRef]]` | >50MB PDF: Redis 조회 → 미스면 분할+업로드+캐시 |
| `_def` | `_resolve_files_api(meta, path) -> tuple[Part, FileRef]` | >20MB 파일: Redis 조회 → 미스면 업로드+캐시 |
| const | `_INLINE_LIMIT` | 20MB |
| const | `_PDF_SPLIT_LIMIT` | 50MB |

### `file_handler/cache_store.py` — Redis CRUD

| 종류 | 이름 | 설명 |
|------|------|------|
| `class` | `FileCacheStore(BaseJsonStore)` | Redis 파일 캐시 (prefix: file_cache) |
| `def` | `make_context_cache_key(file_checksums, model, system_instruction) -> str` | checksums+model+system → SHA-256 hash_key |
| `def` | `find_files_api(file_checksum) -> dict \| None` | Redis에서 Files API URI 조회 |
| `def` | `save_files_api(file_checksum, doc, ttl) -> None` | Redis에 Files API URI 저장 |
| `def` | `find_context_cache(hash_key) -> dict \| None` | Redis에서 context cache name 조회 |
| `def` | `save_context_cache(hash_key, doc, ttl) -> None` | Redis에 context cache name 저장 |
| `def` | `delete_files_api(file_checksum) -> None` | Redis Files API 레코드 삭제 |
| `def` | `delete_context_cache(hash_key) -> None` | Redis context cache 레코드 삭제 |
| const | `FILES_API_TTL` | 169200초 (47시간) |
| const | `CONTEXT_CACHE_DEFAULT_TTL` | 3600초 (1시간) |

### `file_handler/uploader.py` — 업로드 + Context Cache

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `upload_pdf_parts(path, target_mb) -> list[FileRef]` | PDF 분할 → Files API 업로드 (temp 파일 자동 정리) |
| `def` | `create_context_cache(file_refs, *, model, system_instruction, ttl) -> CacheRef` | Gemini context cache 생성 (500/503/429 재시도) |
| const | `_MAX_RETRIES` | 2 |
| const | `_RETRY_DELAY` | 5초 |

### `file_handler/splitter.py` — PDF 분할

| 종류 | 이름 | 설명 |
|------|------|------|
| `def` | `split_pdf(path, target_mb, *, _depth) -> list[str]` | PDF를 target_mb 기준 재귀 분할 + 병합 최적화 (최대 3단계) |
| `_def` | `_estimate_page_sizes(doc, file_size) -> list[float]` | 페이지별 바이트 기여도 추정 |
| const | `_SAFETY_MARGIN` | 95% |
| const | `_MERGE_LIMIT_MB` | 45MB |
| const | `_MAX_DEPTH` | 3 |

---

## Tests

### SDK Tests — `sdk/tests/`

#### `conftest.py`

| 종류 | 이름 | 설명 |
|------|------|------|
| `fixture` | `reset_sdk` (autouse) | 각 테스트 전후 SDK 싱글턴 초기화 |

#### `test_client.py` (8개)

| 테스트 | 설명 |
|--------|------|
| `test_file_checksum` | sha256 prefix, filename, size, mime_type 반환 확인 |
| `test_file_checksum_same_content` | 동일 내용 → 동일 체크섬 |
| `test_file_checksum_different_content` | 다른 내용 → 다른 체크섬 |
| `test_llm_request_async_no_files` | 파일 없이 LLM 요청 (AsyncClient mock) |
| `test_llm_request_async_cache_hit` | MDB 캐시 히트 시 LLM 호출 스킵 |
| `test_done_check` | MDB 캐시 존재 확인 |
| `test_fetch_by_cache_key` | cache_key로 캐시 조회 |
| `test_configure` | SDK 클라이언트 초기화 |

---

### Service Tests — `service/tests/`

#### `conftest.py`

| 종류 | 이름 | 설명 |
|------|------|------|
| `fixture` | `client` | FastAPI TestClient 인스턴스 |
| `fixture` | `mock_collection` | 인메모리 MongoDB 컬렉션 mock (find_one, replace_one) |
| const | `SAMPLE_LLM_INPUT` | 표준 테스트 llm_input |
| const | `SAMPLE_RESPONSE` | mock LLMResponse (text + token usage) |
| const | `SAMPLE_STREAM_CHUNKS` | StreamChunk 리스트 |

#### `test_router_request.py` (6개)

| 테스트 | 설명 |
|--------|------|
| `test_health_check` | /health → 200 + {"status": "ok"} |
| `test_done_check_cache_miss` | /v1/done-check → cache_hit=False, 64자 cache_key |
| `test_done_check_cache_hit` | /v1/done-check → MDB에 캐시 있을 때 반환 |
| `test_request_cache_hit` | /v1/request → cache_mode=True일 때 캐시 즉시 반환 |
| `test_request_cache_off` | /v1/request → cache_mode=False일 때 LLM 호출 |
| `test_fetch_not_found` | /v1/fetch → 존재하지 않는 cache_key → 500 |

#### `test_keying.py` (28개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestBuildLlmInputSubset` | 4 | 필드 추출, None→"", 누락 필드, 커스텀 필드 |
| `TestCanonicalizeLlmInput` | 12 | 결정론, 키 순서 무관, volatile 키 제거, float/Decimal/datetime/date/bytes/None 처리, 중첩 volatile, 리스트 순서 |
| `TestCanonicalizeInput` | 1 | subset → canonicalize 파이프라인 |
| `TestMakeCacheKey` | 4 | 64자 hex, 동일 입력 → 동일 키, 다른 입력 → 다른 키, 비문자열 변환 |
| `TestFileChecksumsInSubset` | 4 | file_checksums 포함/부재, cached_content 제거/유지 |
| `TestFileRefPartNormalization` | 7 | FileRef/Part URI 제거, 동일 checksum 다른 URI → 동일 키 |

#### `test_done_check.py` (6개)

| 테스트 | 설명 |
|--------|------|
| `test_cache_miss` | 캐시 미스 → cached=None |
| `test_cache_hit` | 캐시 히트 → 저장된 문서 반환 |
| `test_no_lookup` | lookup_cache=False → DB 조회 없이 키만 생성 |
| `test_cache_error_fallback` | DB 에러 → cache_error=True + 키 정상 생성 |
| `test_deterministic_key` | 동일 입력 → 동일 키 |
| `test_different_input_different_key` | 다른 입력 → 다른 키 |

#### `test_store.py` (8개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestSaveResult` | 4 | result 포함, elapsed_ms, file_meta 있음/없음 |
| `TestCacheGet` | 2 | 저장된 문서 반환, 없으면 None |
| `TestFetchByCacheKey` | 4 | LLMResponse 반환, StreamChunk 변환, KeyError, 메타 포함 |

#### `test_meta.py` (3개)

| 테스트 | 설명 |
|--------|------|
| `test_extracts_service_name_and_model` | service_name, model 추출 |
| `test_missing_fields_return_none` | 누락 필드 → None |
| `test_empty_input` | 빈 입력 → None |

#### `test_orchestrator.py` (6개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestRunMany` | 4 | 전체 결과 반환, meta에 state 포함, 에러 전파, llm_input 누락 ValueError |
| `TestRunManyStream` | 2 | chunk+done 이벤트, 에러 이벤트, 다중 스트림 인덱싱 |

#### `test_resolver.py` (6개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestInline` | 1 | ≤20MB → 인라인 Part |
| `TestFilesApi` | 2 | >20MB → Files API 업로드, Redis 캐시 히트 |
| `TestContextCache` | 3 | context cache 생성, Redis 재사용, 실패 시 fallback |
| `TestMixed` | 1 | 인라인 + Files API 혼합 |

#### `test_cache_store.py` (12개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestFilesApi` | 4 | save/find, miss→None, delete, split PDF 다중 URI |
| `TestContextCache` | 3 | save/find, miss→None, delete |
| `TestMakeContextCacheKey` | 6 | 동일 입력→동일 키, model/checksum/system 차이→다른 키, 순서 무관, 64자 hex |

#### `test_uploader.py` (6개)

| 테스트 클래스 | 테스트 수 | 검증 항목 |
|---------------|-----------|-----------|
| `TestUploadPdfParts` | 4 | 작은 PDF 무분할, 분할 시 전체 업로드, temp 파일 정리, 에러 시 정리 |
| `TestCreateContextCache` | 3 | 성공, 503 재시도, 비재시도 에러 즉시 실패 |

#### `test_splitter.py` (7개)

| 테스트 | 설명 |
|--------|------|
| `test_small_pdf_returns_original` | ≤target_mb → 원본 반환 |
| `test_split_creates_multiple_parts` | target_mb 초과 → 다중 파트 |
| `test_all_parts_are_valid_pdf` | 분할된 모든 파트 유효한 PDF |
| `test_total_pages_preserved` | 분할 후 총 페이지 수 보존 |
| `test_file_not_found_raises` | 존재하지 않는 파일 → FileNotFoundError |
| `test_single_page_pdf` | 단일 페이지 PDF 처리 |
| `test_max_depth_prevents_infinite_recursion` | 재귀 깊이 제한 (최대 3) |

