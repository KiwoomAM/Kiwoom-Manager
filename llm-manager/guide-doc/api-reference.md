# API Reference

`llm-manager`의 공개 API 명세.

| 영역 | 사용처 | 다루는 내용 |
|---|---|---|
| **SDK** (§2~§10) | Python에서 직접 호출 (`from llm_manager import ...`) | 함수, 입력/반환 모델, 예외 |
| **Service** (§11~§12) | HTTP 호출 (다른 언어/디버깅 시) | 엔드포인트 4개, 요청/응답 스키마 |

대부분의 사용자는 SDK로 충분하다. Service HTTP는 SDK가 그대로 호출하는 인터페이스 — Python 외 언어 접근이나 직접 curl로 찔러볼 때 참고.

## 공개 API 한눈에

| 함수 | 반환 타입 | 자세히 | 설명 |
| --- | --- | --- | --- |
| `llm_request_async` | `tuple[LLMResponse, ResponseMeta]` | [§6](#§6-llm_request_async--llm_request) | 비동기 단건 LLM 호출 |
| `llm_request` | `tuple[LLMResponse, ResponseMeta]` | [§6](#§6-llm_request_async--llm_request) | 동기 단건 (`asyncio.run` 래퍼) |
| `llm_request_stream_async` | `tuple[AsyncIterator[dict], dict]` | [§7](#§7-llm_request_stream_async--llm_request_stream) | 비동기 스트리밍 — `(stream, meta)` |
| `llm_request_stream` | `tuple[Iterator[dict], dict]` | [§7](#§7-llm_request_stream_async--llm_request_stream) | 동기 스트리밍 — `(stream, meta)` |
| `done_check` | `DoneCheckResult` | [§8](#§8-done_check) | 캐시 존재 여부만 확인 (LLM 호출 없음) |
| `fetch_by_cache_key` | `tuple[LLMResponse, ResponseMeta]` | [§9](#§9-fetch_by_cache_key) | 캐시 키로 직접 조회 |
| `close` | `None` (async) | — | SDK `AsyncClient` 커넥션 풀 정리 — agent 종료 전 `await close()` 호출 권장 ([index.md ⚠️](index.md)) |

## `llm_input` 구조

`llm_request_async`의 첫 번째 인자. **`llm_handler.call()`의 파라미터 대부분과 동일**하며, 파일 관련만 `contents`에서 빼고 별도의 `files=[FileInput]` 인자로 분리한 점이 차이다.

`llm_input`엔 텍스트 프롬프트와 생성 파라미터만, 파일은 `FileInput`으로 메타·bytes를 구성해 `files=`로 — Service가 inline / Files API 분류·업로드·캐싱을 자동 처리한다.

### 호출 형태

```python
response, meta = await llm_request_async(
    {
        "contents": "질문",             # 필수. str 또는 [str | Part | FileRef] 혼합 리스트
        "model": "gemini-2.5-flash",
        "system": "시스템 프롬프트",    # 선택
        "temperature": 0,               # 선택
        "max_tokens": 200,              # 선택
        "service_name": "kiwoom-rm",    # 선택 (meta에 기록). 레포 이름과 동일하게 지정 권장
        "tool_config": "auto",          # 선택 ("auto" | "any" | "none")
        "media_resolution": "high",     # 선택
    },
    files=[...],           # 선택. list[FileInput]
    cache_mode=True,       # 기본 True
    save_mode=True,        # 기본 True
    timings={"mdb_ttl": 3600},  # 선택. Timings TypedDict (§4 참고)
    debug=False,           # 기본 False. True면 meta.route_reason 포함
    database=None,         # 기본 None → env MONGODB_DATABASE 또는 kw_ai
)
```

### `llm_input` 키 — `llm_handler.call()` 전체 파라미터

`llm_input` dict에 넣을 수 있는 키 전체 목록 (시그니처 그대로).

```python
def call(
    contents: ContentsType,              # 필수. str | list[str | FileRef | Part]
    *,
    system: str | None = None,           # 시스템 프롬프트
    model: str | None = None,            # 모델명 (기본: gemini-2.5-flash)
    temperature: float | None = None,    # 0.0(결정적) ~ 2.0(창의적)
    max_tokens: int | None = None,       # 최대 출력 토큰 수
    seed: int | None = None,             # 재현성 시드값
    output: str | None = None,           # "json" | "enum" | "text"
    schema: type | dict | None = None,   # Pydantic / Enum / dict
    thinking: str | None = None,         # "minimal" | "low" | "medium" | "high"
    tools: list[dict] | None = None,     # Function calling / Built-in 도구 정의
    tool_config: str | None = None,      # "auto" | "any" | "none"
    history: list[dict] | None = None,   # 이전 대화 이력
    cached_content: str | None = None,   # Context Cache 이름
    service_name: str | None = None,     # 서비스 식별자 (로깅용)
    media_resolution: str | None = None, # "low" | "medium" | "high" | "ultra_high"
) -> LLMResponse
```

> `contents`는 `llm_handler.call()`의 첫 위치 인자이지만, `llm_input` dict에선 다른 키와 동일하게 dict 키로 넣는다.

스트리밍은 `stream()`으로 동일 파라미터. async 버전은 `llm_handler.aio.stream()`.

## 입력 모델

### `FileInput`

`/v1/request` 호출 시 첨부할 파일 — 5개 meta 필드 + bytes.

```python
from llm_manager import FileInput

file = FileInput(
    file_name="report.pdf",
    file_size=1234567,
    file_mtime=1700000000,        # int (Unix timestamp, 초) 또는 str
    mime_type="application/pdf",
    extension="pdf",
    data=b"...",                  # bytes (JSON 직렬화 시 base64)
)
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `file_name` | `str` | 파일명 (확장자 포함) |
| `file_size` | `int` | 파일 크기 (bytes) |
| `file_mtime` | `int \| str` | 파일 수정 시각 (Unix timestamp, 초 단위) |
| `mime_type` | `str` | MIME 타입 (예: `application/pdf`) |
| `extension` | `str` | 확장자 (점 없이, 예: `pdf`) |
| `data` | `bytes` | 파일 바이트 (JSON 직렬화 시 base64로 변환) |

자세한 입력 규칙(캐시 키 영향, 브라우저 mtime 등)은 [file-handling.md §2](file-handling.md#fileinput-입력-규칙) 참고.

### `FileKeyingMeta`

`/v1/done-check` 경량 메타 — 캐시 키 계산용 3 필드만.

```python
from llm_manager import FileKeyingMeta

meta = FileKeyingMeta(
    file_name="report.pdf",
    file_size=1234567,
    file_mtime=1700000000,
)
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `file_name` | `str` | 파일명 |
| `file_size` | `int` | 파일 크기 (bytes) |
| `file_mtime` | `int \| str` | 파일 수정 시각 (Unix timestamp) |

`done_check`은 캐시 존재 여부만 확인하므로 bytes 없이 메타만 전달한다.

### `Timings` TypedDict

시간 관련 설정을 하나의 TypedDict로 묶어 전달한다. 일부 키만 지정하면 나머지는 기본값으로 채워진다.

```python
from llm_manager import Timings, llm_request_async

timings: Timings = {
    "mdb_ttl": 3600,
    "context_cache_ttl": 1800,
}
response, meta = await llm_request_async(llm_input, timings=timings)
```

| 키 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `http_timeout` | `float` | `600.0` | SDK → Service HTTP 응답 대기 시간 (초) |
| `mdb_ttl` | `int \| None` | `None` | MDB 응답 캐시 수명 (초). `None` = 무기한 ([caching.md §2.4](caching.md#ttl-주의) 참고) |
| `context_cache_ttl` | `int \| None` | `None` | Gemini Context Cache 수명 (초). `None` = 생성 안 함 (opt-in) |
| `standby_ttl` | `int` | `600` | Cache stampede dedup standby TTL + waiter 대기 timeout (초) |

## 반환 타입

### `LLMResponse`

`llm_request_async` / `fetch_by_cache_key` 등이 반환하는 응답 객체.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | `str \| None` | 모델 응답 텍스트 (Tool calling 시 None일 수 있음) |
| `model` | `str` | 사용된 모델명 |
| `usage` | `TokenUsage \| None` | 토큰 사용량 |
| `finish_reason` | `str \| None` | 종료 사유 (`"STOP"`, `"MAX_TOKENS"`, `"SAFETY"` 등) |
| `function_calls` | `list[FunctionCall] \| None` | 모델이 요청한 함수 호출 |
| `request_id` | `str` | 요청 식별자 |

`TokenUsage`, `FunctionCall` 등은 `llm_handler` re-export 타입 — [user-guide/llm-handler-reexport.md](user-guide/llm-handler-reexport.md) 참고.

### `ResponseMeta`

응답의 두 번째 튜플 원소. 처리 결과 메타데이터.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | `str` | `'live'` (LLM 직접 호출) 또는 `'cache'` (캐시 hit) |
| `cache_hit` | `bool` | 캐시 히트 여부 (`source == 'cache'`와 동일) |
| `cache_key` | `str \| None` | SHA-256 캐시 키 (64자 hex) |
| `metadata` | `dict` | `{"service_name": ..., "model": ...}` (`llm_input`에서 추출) |
| `collection` | `str \| None` | MongoDB 컬렉션명 |
| `route_reason` | `str \| None` | `debug=True`일 때만 — 라우팅 결정 이유 ([observability.md §2](user-guide/observability.md#route_reason-debugtrue) 참고) |
| `file_meta` | `list[dict] \| None` | files 호출 시 file-manager 부여 메타. files 없으면 응답 dict에 키 자체 부재 |

#### `file_meta` 활용 예시

```python
response, meta = await llm_request_async(llm_input, files=[FileInput(...)])

if meta.file_meta:
    for fm in meta.file_meta:
        # fm = {"file_name", "file_size", "file_mtime", "mime_type", "extension", "file_path"}
        save_to_db(fm["file_path"])
```

> 응답 shape — files가 있을 때만 `meta.file_meta` 키가 들어가고, 없으면 키 자체 부재 (MongoDB doc의 `file_meta` 동작과 거울). 옛 SDK 사용 시 `ResponseMeta`가 모르는 키는 Pydantic v2 기본 `extra="ignore"`로 폐기 — 자연 호환.

### `DoneCheckResult`

`done_check`의 반환 — TypedDict.

| 키 | 타입 | 설명 |
| --- | --- | --- |
| `cache_key` | `str` | SHA-256 캐시 키 (64자 hex) |
| `canonical_json` | `str` | 캐시 키 계산에 쓰인 정규화 JSON |
| `cached` | `dict \| None` | 캐시 문서 dict 또는 None |
| `cache_hit` | `bool` | 캐시 hit 여부 (`cached is not None`과 동일) |
| `collection` | `str` | MongoDB 컬렉션명 |

## `llm_request_async` / `llm_request`

단일 LLM 요청을 실행하고 `(LLMResponse, ResponseMeta)` 튜플을 반환한다.

### 시그니처

```python
async def llm_request_async(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[LLMResponse, ResponseMeta]: ...
```

`llm_request`는 동일 시그니처의 sync 버전 — 내부적으로 `asyncio.run(llm_request_async(...))`.

### 파라미터

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | (필수) | 텍스트 프롬프트 + 생성 파라미터 ([§3](#llm_input-구조)) |
| `files` | `list[FileInput] \| None` | `None` | 첨부 파일 리스트 ([§4 FileInput](#fileinput)) |
| `cache_mode` | `bool` | `True` | 캐시 조회 여부. 자세히는 [caching.md §2.2](user-guide/caching.md#cache_mode--save_mode) |
| `save_mode` | `bool` | `True` | 결과를 MDB에 저장할지 |
| `database` | `str \| None` | `None` | DB 명. `None`이면 env `MONGODB_DATABASE` 또는 `kw_ai` |
| `timings` | `Timings \| None` | `None` | 시간 관련 설정 ([§4 Timings](#timings-typeddict)) |
| `debug` | `bool` | `False` | True면 `meta.route_reason`에 라우팅 결정 이유 포함 |

### 반환

`(LLMResponse, ResponseMeta)` 튜플. 각각의 정의는 [§5 반환 타입](#반환-타입).

### 예외

- `ServiceConnectionError` — Service 연결 실패
- `ServiceError` — Service 4xx/5xx 응답

자세히는 [§10 예외](#예외) 또는 [user-guide/error-handling.md](user-guide/error-handling.md).

### 사용 예시

```python
from llm_manager import llm_request_async, FileInput

response, meta = await llm_request_async(
    {"contents": "이 보고서 요약", "model": "gemini-2.5-flash"},
    files=[FileInput(...)],
    timings={"context_cache_ttl": 3600},
    debug=True,
)

print(response.text)
print(meta.cache_hit, meta.route_reason)
```

## `llm_request_stream_async` / `llm_request_stream`

스트리밍 LLM 요청 — `(stream_iterator, meta)` 튜플 반환.
`meta`는 스트림 시작 전 즉시 확인 가능 (cache hit/miss 여부 등).

### 시그니처

```python
async def llm_request_stream_async(
    llm_input: dict,
    *,
    files: list[FileInput] | None = None,
    cache_mode: bool = True,
    save_mode: bool = True,
    database: str | None = None,
    timings: Timings | None = None,
    debug: bool = False,
) -> tuple[AsyncIterator[dict[str, Any]], dict[str, Any]]: ...
```

`llm_request_stream`은 동일 시그니처의 sync 버전 — `Iterator[dict]` 반환.

### 파라미터

[§6 llm_request_async](#llm_request_async--llm_request)와 동일.

### 반환

`(stream, meta)` 튜플:
- `stream` (`AsyncIterator[dict]` / `Iterator[dict]`) — 청크 이벤트 스트림
- `meta` (`dict`) — 처리 메타데이터 (스트림 시작 전 즉시 확인 가능)

각 이벤트 dict 형식:

```python
{
    "type": "chunk",
    "text": str | None,
    "finish_reason": str | None,
    "usage": dict | None,    # 마지막 청크에만
}
```

### 사용 예시

```python
stream, meta = await llm_request_stream_async(
    {"contents": "...", "model": "gemini-2.5-flash"},
)

print(meta["cache_hit"])    # 스트림 시작 전 확인 가능

async for event in stream:
    print(event["text"] or "", end="", flush=True)
```

### ⚠️ 스트림은 끝까지 소비해야 저장됨

`save_mode=True`일 때, 모든 청크를 순회한 뒤에야 MDB에 저장된다.
중간에 `break`하거나 이터레이터를 버리면 **저장이 실행되지 않는다.**

```python
stream, meta = await llm_request_stream_async(...)

async for event in stream:
    if some_condition:
        break  # ⚠️ 저장 안 됨 — 다음 호출 시 cache miss로 LLM 재호출
```

저장이 필요하면 끝까지 소비한 뒤 처리하도록 코드 구성.

## `done_check`

MDB 캐시 존재 여부만 확인 (LLM 호출 없음). `cache_key`와 캐시 문서를 함께 반환한다.

### 시그니처

```python
async def done_check(
    llm_input: dict,
    *,
    files: list[FileKeyingMeta] | None = None,
    database: str | None = None,
) -> DoneCheckResult: ...
```

`done_check`은 **async만** 제공된다.

### 파라미터

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | (필수) | 캐시 키 계산용 입력 ([§3](#llm_input-구조)) |
| `files` | `list[FileKeyingMeta] \| None` | `None` | 파일 메타 (bytes 없이 3 필드만 — [§4 FileKeyingMeta](#filekeyingmeta)) |
| `database` | `str \| None` | `None` | DB 명. `None`이면 기본값 |

> **주의**: `llm_request_async`와 달리 `files=`는 `FileKeyingMeta` 리스트 (bytes 없음). bytes는 캐시 키 계산에 필요 없기 때문.

### 반환

`DoneCheckResult` (TypedDict). 정의는 [§5 반환 타입](#donecheckresult).

### 사용 예시

```python
from llm_manager import done_check, FileKeyingMeta

dc = await done_check(
    {"contents": "이 보고서 요약", "model": "gemini-2.5-flash"},
    files=[FileKeyingMeta(file_name="report.pdf", file_size=1024, file_mtime=1700000000)],
)

print(dc["cache_hit"])     # True / False
print(dc["cache_key"])     # SHA-256 64자
if dc["cache_hit"]:
    print(dc["cached"])    # 캐시 문서 dict
```

캐시 hit이면 `fetch_by_cache_key`로 결과만 빠르게 가져올 수 있다 — [§9 fetch_by_cache_key](#fetch_by_cache_key) 참고.

## `fetch_by_cache_key`

`cache_key`만으로 캐시된 LLM 결과를 직접 조회. `llm_input`을 다시 구성하지 않으므로 키 재계산 비용이 없다.

### 시그니처

```python
async def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[LLMResponse, ResponseMeta]: ...
```

`fetch_by_cache_key`는 **async만** 제공된다.

### 파라미터

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `cache_key` | `str` | (필수) | `done_check` / 다른 호출의 `meta.cache_key` 등에서 받은 SHA-256 64자 hex |
| `database` | `str \| None` | `None` | DB 명. `None`이면 기본값(`kw_ai`) |

### 반환

`(LLMResponse, ResponseMeta)` 튜플. `meta.source == "cache"`, `meta.cache_hit == True`.

### 예외

| 상황 | 예외 |
| --- | --- |
| Service 연결/타임아웃 | `ServiceConnectionError` |
| 그 외 (캐시 미존재, 역직렬화 실패 등) | `ServiceError` |

### 사용 예시

```python
from llm_manager import done_check, fetch_by_cache_key, FileKeyingMeta

dc = await done_check(
    {"contents": "...", "model": "gemini-2.5-flash"},
    files=[FileKeyingMeta(file_name="report.pdf", file_size=1024, file_mtime=1700000000)],
)

if dc["cache_hit"]:
    response, meta = await fetch_by_cache_key(dc["cache_key"])
    print(response.text)
```

사용 시나리오·동기는 [user-guide/caching.md §5](user-guide/caching.md#fetch_by_cache_key--캐시-키로-결과-직접-조회) 참고.

## 예외

SDK는 모든 에러를 `SDKError` 계층으로 던진다.

### 클래스 계층

```
SDKError                       # 기본 — 모든 SDK 예외의 부모
├── ServiceConnectionError    # Service 자체에 연결 실패
└── ServiceError              # Service가 4xx/5xx 응답 반환
```

`SDKError`로 catch하면 두 하위 예외 모두 잡힌다.

### 속성

| 클래스 | 속성 | 타입 | 설명 |
| --- | --- | --- | --- |
| `SDKError` (base) | `message` | `str` | 사람 읽기용 에러 메시지 |
|                  | `status_code` | `int` | HTTP 상태 코드 (`ServiceConnectionError`는 0) |
| `ServiceConnectionError` | (상속) | | 네트워크 오류, 타임아웃 등 |
| `ServiceError` | (상속) + `error_type` | `str \| None` | `'validation'` / `'cache'` / `'file'` / `'file_resolve'` / `'handler'` 등 ([user-guide/error-handling.md §2](user-guide/error-handling.md#service-에러-타입-error_type)) |

### 사용 예시

```python
from llm_manager import (
    llm_request_async,
    SDKError,
    ServiceConnectionError,
    ServiceError,
)

try:
    response, meta = await llm_request_async(llm_input)
except ServiceConnectionError:
    # 네트워크/타임아웃 — 재시도 또는 알림
    ...
except ServiceError as e:
    if e.error_type == "validation":
        # 422 — 입력 문제
        ...
    else:
        # 5xx — 외부/내부 문제
        ...
```

또는 `SDKError`로 한꺼번에:

```python
try:
    response, meta = await llm_request_async(llm_input)
except SDKError as e:
    log.error("llm-manager 호출 실패: %s", e)
    raise
```

## Service HTTP 엔드포인트

FastAPI 서버 (`port 8101`). SDK가 내부적으로 호출하지만 직접 HTTP 요청도 가능 — Python 외 언어 또는 디버깅 시.

기본 URL: `http://192.7.9.45:8101` (`LLM_REQUEST_URL` env로 변경 가능).

### `POST /v1/request`

LLM 호출 + 캐시 조회/저장. SDK [`llm_request_async`](#llm_request_async--llm_request)의 백엔드.

**요청 바디**

| 필드 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | — | llm-handler에 전달할 입력 ([§3](#llm_input-구조)) |
| `files` | `list[FileInput] \| null` | `null` | 파일 리스트 (meta + bytes base64) |
| `cache_mode` | `bool` | `true` | MDB 캐시 조회 여부 |
| `save_mode` | `bool` | `true` | MDB 저장 여부 |
| `ttl_seconds` | `int \| null` | `null` | MDB 캐시 수명 (현재 미동작 — [caching.md §2.4](user-guide/caching.md#ttl-주의)) |
| `database` | `str \| null` | `null` | MDB database 이름 (기본 `kw_ai`) |
| `context_cache_ttl` | `int \| null` | `null` | Gemini Context Cache 수명 (초). `null` = 생성 안 함 |
| `standby_ttl_seconds` | `int` | `600` | Cache stampede dedup 대기 timeout (초) |
| `debug` | `bool` | `false` | `true`면 `meta.route_reason` 포함 |

**응답**

```json
{
  "result": { /* LLMResponse.model_dump() */ },
  "meta": {
    "source": "live | cache",
    "cache_hit": true,
    "cache_key": "sha256...",
    "collection": "agent_llm",
    "route_reason": "...",
    "metadata": {}
  }
}
```

### `POST /v1/request/stream`

스트리밍 LLM 요청. SSE(Server-Sent Events) 형식.

**요청 바디**: `/v1/request`와 동일.

**응답 (SSE)**

```
data: {"type": "chunk", "text": "...", "finish_reason": null}
data: {"type": "chunk", "text": "...", "finish_reason": "STOP", "usage": {...}}
data: [DONE]
```

### `POST /v1/done-check`

MDB 캐시 존재 여부 확인 (LLM 호출 없음). SDK [`done_check`](#done_check)의 백엔드.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `llm_input` | `dict` | 캐시 키 계산 대상 입력 |
| `files` | `list[FileKeyingMeta] \| null` | 파일 메타 (3 필드만, bytes 없이) |
| `database` | `str \| null` | MDB database |

**응답**

```json
{
  "cache_key": "sha256...",
  "canonical_json": "{...}",
  "cached": { /* 저장된 문서 또는 null */ },
  "cache_hit": true,
  "collection": "agent_llm"
}
```

### `POST /v1/fetch`

캐시 키만으로 MDB 결과 조회. SDK [`fetch_by_cache_key`](#fetch_by_cache_key)의 백엔드.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `cache_key` | `str` | 조회할 SHA-256 캐시 키 |
| `database` | `str \| null` | MDB database |

**응답**: `/v1/request`와 동일한 `result` + `meta` 구조.

## 에러 응답 형식

Service는 4xx/5xx 응답 시 표준 JSON으로 답한다.

```json
{
  "error": "에러 메시지",
  "detail": "상세 내용 (선택)",
  "error_type": "validation | cache | file | file_resolve | handler | unsupported_file_type | file_too_large",
  "request_id": "uuid"
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `error` | `str` | 사람 읽기용 메시지 |
| `detail` | `str \| null` | 상세 내용 (선택) — `validation` 에러 시 검증 실패 정보 등 |
| `error_type` | `str` | 에러 분류 (값 목록은 [user-guide/error-handling.md §2](user-guide/error-handling.md#service-에러-타입-error_type)) |
| `request_id` | `str` | 요청 식별자 (UUID) — 서버 로그와 매칭용 |

SDK는 이 응답을 [`ServiceError`](#예외)`(message=error, status_code=<HTTP>, error_type=error_type)`로 변환한다.

자세한 트러블슈팅은 [user-guide/error-handling.md](user-guide/error-handling.md) + [debugging-guide.md](../debugging-guide.md) 참고.
