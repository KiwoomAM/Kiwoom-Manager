# Architecture

`llm-manager`는 두 컴포넌트로 나뉜다 — 에이전트가 설치하는 가벼운 SDK와, 무거운 의존성·캐싱 로직을 담당하는 Service.

## SDK ↔ Service 분리

```
[Agent 프로세스]                    [Service 프로세스 (FastAPI 8101)]
─────────────────                  ──────────────────────────────────
llm_manager                        kiwoom-llm-handler
└── httpx                          kiwoom-mdb-handler
                                   kiwoom-redis-handler
   POST /v1/request    ─────────►  kiwoom-file-manager
       (HTTP/JSON)                 PyMuPDF
                                   FastAPI / uvicorn
```

| 영역 | 책임 | 의존성 |
|---|---|---|
| **SDK** | HTTP 클라이언트, 입력 검증, 응답 파싱 | `httpx`, `kiwoom-llm-handler` (re-export 용) |
| **Service** | 캐싱, 파일 처리, LLM 호출, dedup | MDB / Redis / LLM Handler / file-manager / PyMuPDF |

### 왜 분리했나

1. **에이전트 의존성 최소화** — 여러 에이전트 레포가 SDK만 설치 (httpx 1개) → 무거운 의존성 폭발 회피
2. **캐싱 로직 단일화** — MDB·Redis 접근을 Service가 일괄 담당. 에이전트마다 캐시 정합성 챙기지 않아도 됨
3. **운영 변경 격리** — Gemini SDK 버전, MDB 인덱스 등 변경 시 Service만 재배포

## 디렉토리 구조

```
packages/llm-manager/
├── sdk/
│   ├── llm_manager/
│   │   ├── __init__.py        # 공개 API + close + Timings TypedDict
│   │   ├── _client.py         # HTTP 클라이언트 (httpx.AsyncClient 싱글톤)
│   │   ├── aio.py             # llm_handler.aio re-export
│   │   ├── exceptions.py      # SDKError 계층
│   │   └── models.py          # FileInput, FileKeyingMeta, ResponseMeta 등
│   └── pyproject.toml
│
├── service/
│   ├── app/
│   │   ├── main.py            # FastAPI 앱 (port 8101) + 미들웨어
│   │   ├── config.py          # 환경변수
│   │   ├── core/
│   │   │   ├── errors.py      # LLMRequestServiceError 계층
│   │   │   └── providers.py   # MDB / Redis / LLM 외부 서비스 연결
│   │   ├── models/
│   │   │   ├── requests.py    # 요청 DTO (LLMRequestBody, FileInput 등)
│   │   │   └── responses.py   # 응답 DTO (LLMRequestResponse, ErrorResponse)
│   │   ├── routers/
│   │   │   └── request.py     # 4개 엔드포인트 (/v1/request, /stream, /done-check, /fetch)
│   │   └── logic/
│   │       ├── runner.py        # 메인 실행 흐름 (cache → file → LLM → save)
│   │       ├── keying.py        # 캐시 키 생성 (canonicalize → SHA-256)
│   │       ├── done_check.py    # 캐시 조회
│   │       ├── store.py         # MDB 저장/조회
│   │       ├── meta.py          # meta dict 생성
│   │       ├── file_register.py # file-manager 등록 (Phase 2)
│   │       ├── file_resolver.py # stored_files → FileInput 변환 (Phase 3)
│   │       └── file_processing/
│   │           ├── resolver.py     # 파일 처리 진입점 (inline/Files API/Context Cache 분기)
│   │           ├── validators.py   # mime 화이트리스트 + 2GB 검사
│   │           ├── cache_store.py  # Redis URI/Context Cache 저장
│   │           ├── dedup.py        # Cache stampede dedup (SETNX)
│   │           ├── uploader.py     # Files API 업로드 + create_cache
│   │           └── splitter.py     # PDF 분할 (50MB 초과 시)
│   ├── pyproject.toml
│   └── tests/                 # Service 테스트
│
├── sdk/tests/                 # SDK 테스트
└── quickstarts/
    ├── quickstart_sdk.ipynb
    └── verify_all.ipynb
```

각 파일의 책임은 §4 (SDK) / §5 (Service)에서 자세히 다룬다.

## 전체 흐름도

`llm_request_async` 호출이 SDK에서 시작해 Service의 어느 모듈을 거치는지.

```
[SDK]                                  [Service]                              [외부]
─────                                  ─────────                              ─────

llm_request_async()
  │
  │ 1. _resolve_timings (기본값)
  │ 2. AsyncClient.request
  │    (HTTP/JSON, bytes는 base64)
  │     POST /v1/request
  ├────────────────────────────────► routers/request.py:handle_request
  │                                      │
  │                                      ▼
  │                                  runner.llm_request_async()
  │                                      │
  │                                      │ ① _prepare_files
  │                                      │    ├─ file_resolver (Phase 3) — stored_files를 placeholder FileInput으로
  │                                      │    └─ file_register ──────────────► file-manager (POST /upload)
  │                                      │
  │                                      │ ② prepare
  │                                      │    ├─ keying.canonicalize → SHA-256
  │                                      │    ├─ validators (mime / 2GB 검사)
  │                                      │    └─ done_check.cache_get ───────► MDB
  │                                      │       ├─ HIT  → cached_output 반환
  │                                      │       └─ MISS ↓
  │                                      │    file_processing/resolver.py
  │                                      │    ├─ 분류 (inline_pool / files_api_pool)
  │                                      │    ├─ uploader ───────────────────► Gemini Files API
  │                                      │    │  + cache_store ──────────────► Redis (URI 캐시)
  │                                      │    └─ Context Cache (opt-in) ────► Gemini + Redis
  │                                      │
  │                                      │ ③ agent_llm dedup ────────────────► Redis (SETNX)
  │                                      │    (save_mode=True 일 때만 — docs/35)
  │                                      │    ├─ leader → ④
  │                                      │    └─ waiter → 대기 후 캐시 재조회
  │                                      │
  │                                      │ ④ _do_llm_call
  │                                      │    llm_handler.aio.call ─────────► LLM Handler (8100)
  │                                      │
  │                                      │ ⑤ save_result ────────────────────► MDB
  │                                      │
  │ 3. LLMResponse + ResponseMeta parse  │ ⑥ 응답 반환
  ◄──────────────────────────────────────┤
  │           HTTP 응답
  ▼          (LLMRequestResponse)
(response, meta) 튜플
```

각 단계의 모듈은 §4 (SDK) / §5 (Service)에서 자세히 다룬다.

## SDK 구조

총 5개 파일. 각 파일의 책임과 주요 export.

### `__init__.py` — 공개 API + lifecycle

| 위치 | 내용 |
|---|---|
| 함수 | `llm_request_async`, `llm_request`, `llm_request_stream_async`, `llm_request_stream`, `done_check`, `fetch_by_cache_key`, `close` |
| 타입 | `Timings` (TypedDict) |
| 싱글톤 | `_get_client()` — `httpx.AsyncClient` 1개를 lazy 생성 후 재사용 |
| Re-export | `llm_handler` 모듈 + public symbols (`call`, `stream`, `embed`, `Part`, `LLMResponse` 등) |

### `_client.py` — HTTP 클라이언트

`AsyncClient` 클래스 — Service의 4개 엔드포인트를 호출.

| 메서드 | 엔드포인트 |
|---|---|
| `request()` | `POST /v1/request` |
| `request_stream()` | `POST /v1/request/stream` (SSE 파싱) |
| `done_check()` | `POST /v1/done-check` |
| `fetch()` | `POST /v1/fetch` |

내부 `_request()`가 모든 호출을 통과 — `httpx.ConnectError`/`TimeoutException` → `ServiceConnectionError`로 변환, 4xx/5xx → `ServiceError(error_type=...)`.

### `aio.py` — `llm_handler.aio` re-export

`from llm_manager import aio` 사용 시 `llm_handler.aio`의 모든 public 심볼이 그대로 노출.
사용자가 `llm_handler` 별도 의존성 추가 없이 비동기 API 사용 가능 — [user-guide/llm-handler-reexport.md](../user-guide/llm-handler-reexport.md).

### `exceptions.py` — SDK 예외

```
SDKError                       # 기본
├── ServiceConnectionError    # 네트워크/타임아웃
└── ServiceError              # Service 4xx/5xx (error_type 속성)
```

자세히는 [api-reference.md §10](../api-reference.md#예외).

### `models.py` — Pydantic 입력/반환 모델

| 클래스 | 용도 |
|---|---|
| `FileInput` | `/v1/request` 파일 첨부 (5 meta + bytes) |
| `FileKeyingMeta` | `/v1/done-check` 경량 메타 (3 필드) |
| `StoredFile` | 이미 등록된 파일 참조 (5 meta + `file_path`) — Phase 2/3 흐름 |
| `ResponseMeta` | 응답 meta 객체 |
| `ResponseResult` | `/v1/request` raw 응답 (TypedDict) |
| `DoneCheckResult` | `/v1/done-check` raw 응답 (TypedDict) |

> **`Base64Field`** — bytes를 JSON 직렬화 시 base64로 자동 변환하는 Pydantic 커스텀 타입. `FileInput.data`에 사용. Service의 `requests.py`에 동일 정의가 있음 (의도된 중복 — SDK ↔ Service는 독립 패키지라 모듈 공유 불가).

## Service 구조

진입점부터 비즈니스 로직 순서로 정리.

### 5.1 진입점 — `main.py` + `config.py`

**`main.py`** — FastAPI 앱 (port 8101).

| 구성 | 책임 |
|---|---|
| `app = FastAPI(...)` + `lifespan` | 시작 시 `configure_handlers()` 호출, 종료 시 file-manager AsyncClient close |
| `@app.exception_handler(LLMRequestServiceError)` | 도메인 예외를 `ErrorResponse` JSON으로 변환 |
| `@app.exception_handler(RequestValidationError)` | FastAPI 검증 실패 → 422 |
| `@app.middleware("http")` | 매 요청마다 `request_id` UUID 생성, 미처리 예외 catch → 500 generic |
| `GET /health` | 헬스 체크 |

**`config.py`** — `pydantic-settings` 기반 `Settings` 클래스. env / `.env`로 외부 서비스 URL, MongoDB DB, 포트 설정.

### 5.2 `core/`

**`errors.py`** — Service 도메인 예외 계층.

```
LLMRequestServiceError (base)        # status_code, error_type
├── CacheError                        # 502, "cache"
├── FileUploadError                   # 502, "file"
├── FileResolveError                  # 502, "file_resolve"
├── ValidationError                   # 422, "validation"
├── UnsupportedFileTypeError          # 422, "unsupported_file_type"
└── FileTooLargeError                 # 422, "file_too_large"
```

`main.py`의 exception handler가 이 계층을 catch해 `ErrorResponse`로 변환.

**`providers.py`** — 외부 서비스 연결 관리.

| 함수 | 역할 |
|---|---|
| `configure_handlers()` | 시작 시 MDB / Redis / LLM Handler URL 주입 (싱글톤 초기화) |
| `get_redis()` | Redis 클라이언트 반환 |
| (startup) standby flush | 서버 재시작 시 잔존 standby 키 정리 (cache stampede dedup) |

### 5.3 `models/` + `routers/`

**`models/requests.py`** — 입력 DTO (Pydantic).

| 클래스 | 용도 |
|---|---|
| `FileInput` | `/v1/request` 파일 (5 meta + bytes) — SDK 모델과 거울 |
| `FileKeyingMeta` | `/v1/done-check` 경량 메타 |
| `StoredFile` | Phase 2/3 파일 참조 (5 meta + file_path) |
| `LLMRequestBody` | `/v1/request` 전체 바디 |
| `DoneCheckBody` | `/v1/done-check` 바디 |
| `FetchBody` | `/v1/fetch` 바디 |

> SDK `models.py`와 의도된 중복 — 양쪽 패키지가 독립이므로 동기화 필요.

**`models/responses.py`** — 응답 DTO.

| 클래스 | 용도 |
|---|---|
| `LLMRequestResponse` | `/v1/request` (+ stream) 응답 |
| `DoneCheckResponse` | `/v1/done-check` 응답 |
| `FetchResponse` | `/v1/fetch` 응답 |
| `ErrorResponse` | 모든 에러 응답 (error / detail / error_type / request_id) |

**`routers/request.py`** — 4개 엔드포인트 핸들러.

| 핸들러 | 엔드포인트 | 위임 대상 |
|---|---|---|
| `handle_request` | POST /v1/request | `runner.llm_request_async` |
| `handle_request_stream` | POST /v1/request/stream (SSE) | `runner.llm_request_stream_async` |
| `handle_done_check` | POST /v1/done-check | `done_check.done_check` |
| `handle_fetch` | POST /v1/fetch | `store.fetch_by_cache_key` |

핸들러는 입력 검증과 응답 wrapping만 — 실제 로직은 `logic/` 모듈로 위임.

### 5.4 `logic/` — 핵심 비즈니스 로직

**`runner.py`** — 가장 큰 모듈. 라우터가 직접 호출하는 entry point들과 내부 흐름 조정.

| 공개 함수 | 책임 |
|---|---|
| `llm_request_async` / `llm_request` | 단건 LLM 호출 (cache → file 처리 → LLM → save) |
| `llm_request_stream_async` / `llm_request_stream` | 스트리밍 버전 |

| 내부 함수 | 책임 |
|---|---|
| `_prepare_files` | Phase 3 (`file_resolver`) + Phase 2 (`file_register`) 호출 |
| `prepare` | keying / validators / done_check 통합 (cache 조회까지) |
| `finalize` | 결과 정리 + MDB save |
| `_do_llm_call` | 실제 `llm_handler.aio.call` 실행 |
| `_recheck_agent_llm_cache` | dedup waiter가 깨어났을 때 캐시 재조회 |
| `_is_gemini_404` | Files API URI 만료(404) 감지 → cache invalidate 트리거 |

흐름은 [§3 전체 흐름도](#전체-흐름도) 참고. agent_llm dedup 로직(SETNX + waiter 대기)이 여기 박혀있다.

**`keying.py`** — 캐시 키 생성.

| 함수 | 역할 |
|---|---|
| `make_cache_key(llm_input)` | subset 추출 → canonicalize → SHA-256 → 64자 hex |
| `make_file_checksum(meta)` | `(file_name, file_size, file_mtime)` 3 필드 → SHA-256 |
| `canonicalize_llm_input(...)` | 재귀적 정규화 (volatile 키 제거, 타입별 변환) |

자세한 알고리즘은 [user-guide/caching.md §6](../user-guide/caching.md#참고-캐시-키-상세).

**`done_check.py`** — 캐시 조회.

`done_check()` 함수가 캐시 키 생성(keying) + MDB 조회(store)를 묶어 `DoneCheckResult` 반환. `runner.prepare`가 내부적으로 호출하기도 하고, `/v1/done-check` 엔드포인트가 직접 호출하기도 함.

**`store.py`** — MDB 저장/조회.

| 함수 | 역할 |
|---|---|
| `cache_get(cache_key, collection, database)` | MDB에서 캐시 문서 조회 |
| `cache_set(doc, collection, database)` | upsert |
| `save_result(...)` | 호출 결과를 표준 문서 형태로 저장 |
| `fetch_by_cache_key(cache_key, *, database)` | `/v1/fetch` 백엔드 — 문서 없으면 `KeyError`, 역직렬화 실패 시 `ValueError` |

> `fetch_by_cache_key`의 `KeyError`/`ValueError`는 라우터에서 잡지 않아 미들웨어의 generic 500으로 변환됨 — backlog.

**`meta.py`** — meta 정보 생성.

`build_meta(llm_input)` — `service_name`, `model` 등을 추출해 MDB 저장용 metadata dict 생성.
