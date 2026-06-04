# 1. 개요

---

Agent에서 LLM을 호출할 때 **중복 호출 없이 이전 응답을 재사용**하는 캐싱 레이어.
같은 질문 + 같은 파일이면 LLM을 다시 부르지 않고 MongoDB에서 즉시 반환한다.

**캐시 3계층**으로 낭비를 최소화한다:
- **LLM 응답 캐시 (MongoDB)**: 동일 입력 → 저장된 응답 반환
- **Files API URI 캐시 (Redis)**: 같은 파일 재업로드 방지 (47h)
- **Context Cache (Gemini)**: 반복 사용 파일의 입력 토큰 90% 절감 (opt-in)

파일 처리는 Google 공식 문서 기준으로 구현되어 있어, **사용자는 파일을 넘기는 방법만 알면 된다.**
크기·타입에 따른 inline / Files API 전환, PDF 50MB 초과 시 자동 분할은 내부에서 처리한다.

- **Quickstart**: `quickstarts/quickstart_sdk.ipynb` — 간단한 예시 실행 가능한 코드

# 2. 핵심 특징

---

| 항목 | 설명 |
| --- | --- |
| 패키지명 | `kiwoom-llm-request` |
| 버전 | `0.3.0` |
| Python | **`>=3.11`** |
| 구성 | **순수 SDK manager** (별도 서버 없음). agent 프로세스 안에서 직접 실행 |
| 의존성 | `kiwoom-llm-handler`, `kiwoom-mdb-handler`, `kiwoom-redis-handler`, `kiwoom-file-manager`, `pydantic`, `filetype`, `PyMuPDF` |
| 동작 방식 | SDK가 handler(mdb·redis·llm·file) 서버들을 **직접 조합 호출**. (구 0.2.0의 FastAPI Service(8101) 레이어는 제거됨 — 근거: [docs/23](docs/23.manager-sdk-refactoring-plan.md)) |

# 3. 디렉터리 구조

```
packages/llm-request/
├── llm_request/                  # 단일 SDK manager 패키지
│   ├── __init__.py               # 공개 API (llm_request_async 등)
│   ├── aio.py                    # llm_handler.aio re-export
│   ├── models.py                 # StoredFile, RequestMeta, FileKeyingMeta, Expire* (+ 내부 FileInput)
│   ├── exceptions.py             # 공개 예외 (LLMRequestServiceError 계층)
│   └── _internal/                # manager 로직 (외부 사용 X)
│       ├── runner.py             # LLM 호출 (캐시 조회/저장 + standby dedup)
│       ├── keying.py             # 캐시 키 생성
│       ├── done_check.py         # 캐시 조회
│       ├── store.py              # MongoDB 캐시 저장/조회
│       ├── meta.py               # 저장용 메타 생성
│       ├── gemini_errors.py      # Gemini 404 / too-small 판별
│       ├── context_cache_lifecycle.py  # context cache expire
│       ├── stored_file_resolver.py     # stored_files → FileInput (lazy fetch)
│       ├── _config.py            # 하드코딩 handler URL + lazy 초기화
│       └── file_processing/
│           ├── resolver.py       # 파일 분류 (inline / Files API / context cache)
│           ├── uploader.py       # Files API 업로드 + context cache 생성
│           ├── cache_store.py    # URI 캐시 (Redis)
│           ├── dedup.py          # cache stampede dedup
│           ├── splitter.py       # PDF 분할
│           └── validators.py     # 파일 유효성 검사
├── tests/
├── quickstarts/
│   ├── quickstart_sdk.ipynb
│   └── verify_all.ipynb
└── pyproject.toml
```


# 4. 설정 및 준비사항

## 설치

```bash
pip install -e ./packages/llm-request
```

별도 서버를 띄울 필요가 없다. 첫 호출 시 SDK가 handler 들을 하드코딩된 사내 URL로 자동 초기화한다 (`LLM_REQUEST_URL` 같은 설정 불필요).

## 환경변수 (선택 — 기본값 내장)

handler URL 기본값이 사내 주소이므로 보통 그대로 두면 된다. 다른 주소를 쓸 때만 환경변수로 override.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_HANDLER_URL` | `http://192.7.9.45:8100` | LLM Handler 서비스 |
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | MDB Handler 서비스 |
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | Redis |
| `FILE_MANAGER_URL` | `http://192.7.9.45:8600` | file-manager 서비스 |
| `MONGODB_DATABASE` | `kw_ai` | MongoDB 데이터베이스명 (`database=` 인자로도 지정 가능) |

## 데이터베이스 안내

| 환경 | `MONGODB_DATABASE` | 용도 |
| --- | --- | --- |
| 운영/에이전트 | `kw_ai` | 실제 에이전트 배포 시 사용 |
| 개발/테스트 | `kw_ai_dev` | Quickstart, 기능 테스트, 프롬프트 실험 |

개발 중 불필요한 데이터가 쌓이지 않도록 `save_mode=False` 또는 `database="kw_ai_dev"` 권장.


# 5. Quick Start

---

## 텍스트만

```python
from llm_request import llm_request_async

response, meta = await llm_request_async(
    {"contents": "대한민국의 수도는?", "model": "gemini-2.5-flash"},
)
print(response.text)
print(meta.source)   # 'live' 또는 'cache'
```

## 파일 첨부 — `register_or_get` + `stored_files`

agent 가 file-manager SDK 의 `register_or_get` 으로 파일을 등록한 후, 받은 6 필드 dict 를 `StoredFile` 로 변환해 `stored_files=` 인자로 전달한다. llm-request 는 `file_path` 만 받아 file-manager 에서 직접 fetch 한다.

### `StoredFile` 6 필드

| 필드 | 설명 |
| --- | --- |
| `file_name` | 파일명 (확장자 포함) |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 파일 수정 시각 (Unix timestamp, int) |
| `mime_type` | MIME 타입 (예: `application/pdf`) |
| `extension` | 확장자 (예: `.pdf`, 점 포함) |
| `file_path` | file-manager 가 부여한 UUID (`register_or_get` 결과) |

> **⚠️ file_name, file_size, file_mtime 은 실제 파일에서 추출한 값과 반드시 일치해야 한다.**
> 이 세 값은 캐시 키 생성에 직접 사용된다. 잘못된 값을 넣으면 캐시 미스가 반복 발생한다.

```python
from pathlib import Path
from file_manager import FileManagerHandler
from llm_request import llm_request_async, StoredFile

fm = FileManagerHandler()

# 1. file-manager 등록 — build_metadata 로 5 필드 dict 생성 후 register_or_get
p = Path("report.pdf")
bytes_data = p.read_bytes()
metadata = fm.build_metadata(
    file_name=p.name,
    file_mtime=int(p.stat().st_mtime),
    file_size=p.stat().st_size,
)
sf_dict = await fm.register_or_get(bytes_data, metadata)
del bytes_data  # bytes release — agent 메모리 회수

# 2. llm-request 호출 (file_path + 5 메타만 전송)
response, meta_resp = await llm_request_async(
    {"contents": "이 파일을 분석해줘", "model": "gemini-2.5-flash"},
    stored_files=[StoredFile(**sf_dict)],
)
print(response.text)
print(meta_resp.file_meta[0]["resolved_via"])  # "primary"
```

> **⚠️ Frontend 업로드 경로:** 위 예시는 서버 파일시스템에서 읽는 케이스. 브라우저 → 에이전트 backend 로 파일 업로드받는 경우 `file_mtime` 을 **frontend 가 명시 전달**해야 한다 (HTTP multipart 는 자동 전달 안 함). 빠뜨리면 같은 파일이 매 업로드마다 새 레코드로 쌓여 캐시 무의미. 상세는 [file-manager README](../file-manager/README.md#반드시-확인--frontend에서-파일을-받는-경우) 참고.

## 같은 파일 재호출

`register_or_get` 결과 dict 를 자기 DB 에 저장해두고 재호출 시 그대로 사용. file-manager 등록 / bytes 처리 모두 skip.

```python
# 첫 호출 시 sf_dict 자기 DB 에 저장
db.evaluations.insert_one({"file_meta": sf_dict, ...})

# 재호출 — bytes 처리 자체가 없음
saved = db.evaluations.find_one({...})["file_meta"]
ref = StoredFile(**saved)
response, _ = await llm_request_async(
    {"contents": "다른 질문", "model": "gemini-2.5-flash"},
    stored_files=[ref],
)
```

> ⚠️ **응답 `meta.file_meta` 를 저장한 경우 — `StoredFile(**...)` 시 422**
>
> 응답 `meta.file_meta[i]` 에는 server-fill `resolved_via` 가 박혀서 내려옴. agent 가 응답 dict 를 자기 DB 에 저장했다가 재호출 시 그대로 `StoredFile(**dict)` 로 넣으면 `extra="forbid"` 정책상 ValidationError. 6 필드만 픽킹 필요:
>
> ```python
> saved = db.evaluations.find_one({...})["meta_file_meta"]
> ref = StoredFile(**{k: saved[k] for k in StoredFile.model_fields})
> ```
>
> `register_or_get` 결과 (`sf_dict`) 를 저장했다면 6 필드만 있으므로 `StoredFile(**saved)` 로 바로 사용 가능 (위 예시).

### `resolved_via` 값

응답 `meta.file_meta[i].resolved_via` 가 fetch 경로를 알려준다 (디버깅 용도).

| 값 | 의미 |
| --- | --- |
| `"primary"` | `file_path` 1차 fetch 성공 |
| `"fallback"` | `file_path` stale → `check_cache` 로 새 path 발견 후 fetch. 호출자가 자기 DB 의 `file_path` 를 새 값으로 갱신 권장 |

> ⚠️ `resolved_via` 필드 도입 이전에 MDB 에 저장된 옛 doc 의 cache hit 응답에는 이 키가 없을 수 있다. 직접 인덱싱 (`fm["resolved_via"]`) 대신 `fm.get("resolved_via")` 사용 권장.

### 에러 처리 — `FileResolveError`

`get_file(file_path)` 1차 404 + `check_cache(hashing 3)` miss → `FileResolveError` raise (error_type='file_resolve'). caller 는 이를 catch 후 `register_or_get` 으로 재등록하여 재호출.

```python
from llm_request import llm_request_async, StoredFile, FileResolveError

try:
    response, _ = await llm_request_async(llm_input, stored_files=refs)
except FileResolveError:
    # file-manager 에 정말로 없음 → 파일 재등록
    sf_dict = await fm.register_or_get(p.read_bytes(), fm.build_metadata(...))
    response, _ = await llm_request_async(
        llm_input, stored_files=[StoredFile(**sf_dict)],
    )
```


# 6. SDK

---

## 공개 API

| 함수 | 반환 타입 | 설명 |
| --- | --- | --- |
| `llm_request_async` | `tuple[LLMResponse, RequestMeta]` | 비동기 단건 LLM 호출 |
| `llm_request` | `tuple[LLMResponse, RequestMeta]` | 동기 단건 (`asyncio.run` 래퍼) |
| `llm_request_stream_async` | `tuple[AsyncIterator[dict], dict]` | 비동기 스트리밍 — `(stream, meta)` 튜플 |
| `llm_request_stream` | `tuple[Iterator[dict], dict]` | 동기 스트리밍 — `(stream, meta)` 튜플 |
| `done_check` | `DoneCheckResult` | 캐시 존재 여부만 확인 (LLM 호출 없음) |
| `fetch_by_cache_key` | `tuple[LLMResponse, RequestMeta]` | 캐시 키로 직접 조회 |
| `delete_cache_async` | `ExpireResult` | Context Cache 를 grace_seconds 후 삭제 (비동기) |
| `delete_cache` | `ExpireResult` | Context Cache 를 grace_seconds 후 삭제 (`asyncio.run` 래퍼) |
| `close` | `None` (async) | 하위호환 no-op (httpx 클라이언트 제거됨). 호출해도 안전 |

## llm_input 구조

`llm_request_async`의 첫 번째 인자. **`llm_handler.call()`의 파라미터 대부분과 동일하며, 파일 관련만 `contents`에서 빼고 별도의 `stored_files=[StoredFile]` 인자로 분리**한 점이 차이다. `llm_input`에는 텍스트 프롬프트와 생성 파라미터만 넣고, 파일은 file-manager `register_or_get` 으로 등록 후 받은 6 필드를 `StoredFile` 로 구성해 `stored_files=` 로 넘기면 Service 가 file-manager 에서 bytes 를 fetch 해 inline / Files API 분류·업로드·캐싱을 자동 처리한다.

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
        "media_resolution": "high",     # 선택 ("low" | "medium" | "high")
    },
    stored_files=[...],    # 선택. list[StoredFile] — file-manager register_or_get 결과
    cache_mode=True,       # 기본 True
    save_mode=True,        # 기본 True
    timings={"mdb_ttl": 3600},  # 선택. Timings TypedDict
    debug=False,           # 기본 False. True면 meta.route_reason 포함
    database=None,         # 기본 None → env MONGODB_DATABASE 또는 kw_ai
)
```

### llm_handler.call() 전체 파라미터

`llm_input` dict의 키로 사용할 수 있는 전체 목록.

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
    media_resolution: str | None = None, # "low" | "medium" | "high"
) -> LLMResponse
```

스트리밍은 `stream()`으로 동일 파라미터 사용. async 버전은 `llm_handler.aio.stream()`.

## cache_mode / save_mode

조합별 흐름은 [8. Runner — 캐시 조회 및 LLM 호출 흐름](#8-runner) 참고.

## Timings TypedDict

시간 관련 설정을 하나의 TypedDict로 묶어서 전달한다. 일부 키만 지정하면 나머지는 기본값으로 채워진다.

| 키 | 기본값 | 역할 | 조정 기준 |
| --- | --- | --- | --- |
| `http_timeout` | `600.0` | SDK → Service HTTP 응답 대기 시간 (초) | 느린 모델 사용 시 늘릴 것. 기본 600초로 대부분 충분 |
| `mdb_ttl` | `None` | MDB 캐시 수명 (초). None = 영구 저장 | **현재 미동작 — 설정해도 만료 처리 안 됨. 항상 영구 저장** |
| `context_cache_ttl` | `None` | Gemini Context Cache 수명 (초) | **`None` 또는 `0` 이면 Context Cache 비활성**. 양수(`>0`) 값만 생성 트리거. 반복 참조 파일에 Context Cache 를 쓰려면 양수 값 지정 필요 (예: `3600`) |
| `standby_ttl` | `600` | Cache stampede dedup 대기 시간 (초) | 동일 요청이 동시에 몰릴 때 중복 LLM 호출 방지. LLM 응답 시간보다 길게 설정 |

```python
response, meta = await llm_request_async(
    {"contents": "...", "model": "gemini-3-flash-preview"},
    timings={
        "context_cache_ttl": 3600,  # Context Cache 사용 시 필수
    },
)
```

## debug

`debug=True` 설정 시 `meta.route_reason`에 해당 요청이 어떤 경로로 처리됐는지가 기록된다(기본 `False`). 값의 의미는 다음과 같다.

```
"cache_hit"              # 캐시 히트
"cache_miss"             # 캐시 미스
"cache_error_fallback"   # 캐시 조회 오류 → LLM fallback
"cache_deserialize_error" # 캐시 역직렬화 실패 → LLM 재호출
"standby_wait_hit"       # Dedup standby 대기 중 다른 요청이 완료
"cache_off_save_off"     # cache_mode=False, save_mode=False
"cache_off_save_on"      # cache_mode=False, save_mode=True
```

## 반환 타입

### LLMResponse

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | `str \| None` | 모델 응답 텍스트 (Tool calling 시 None일 수 있음) |
| `model` | `str` | 사용된 모델명 |
| `usage` | `TokenUsage \| None` | 토큰 사용량 |
| `finish_reason` | `str \| None` | 종료 사유 (`"STOP"`, `"MAX_TOKENS"`, `"SAFETY"` 등) |
| `function_calls` | `list[FunctionCall] \| None` | 모델이 요청한 함수 호출 |
| `request_id` | `str` | 요청 식별자 |

### RequestMeta

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | `str` | `'live'` (LLM 직접 호출) 또는 `'cache'` |
| `cache_hit` | `bool` | 캐시 히트 여부 |
| `cache_key` | `str \| None` | SHA-256 캐시 키 (64자) |
| `metadata` | `dict` | `{"service_name": ..., "model": ...}` |
| `collection` | `str \| None` | MongoDB 컬렉션명 |
| `route_reason` | `str \| None` | `debug=True`일 때만 포함 |
| `file_meta` | `list[dict] \| None` | `stored_files` 인자로 호출 시 각 `StoredFile` 의 6 필드(`file_path` 포함)가 담긴 list. stored_files 없는 호출이면 `None` (응답 dict 에는 키 자체 부재). 호출자가 자기 DB 에 저장 후 재호출 등에 활용 가능 |
| `context_cache_id` | `str \| None` | Context Cache 생성/HIT 시 노출되는 식별자 (opaque). `delete_cache_async` 호출에 사용. live 호출일 때만 노출되며, MDB cache HIT 응답에는 키 자체 부재. **영구 식별자가 아니다** — Gemini 측 TTL 이 만료되면 자동 파괴되고, 같은 입력으로 재호출 시 새 cache (새 id) 가 생성된다 |

#### `file_meta` 활용 예시

```python
response, meta = await llm_request_async(llm_input, stored_files=[StoredFile(...)])

if meta.file_meta:
    for fm in meta.file_meta:
        # fm = {"file_name", "file_size", "file_mtime", "mime_type", "extension", "file_path"}
        save_to_db(fm["file_path"])
```

> 응답 shape — stored_files 가 있을 때만 `meta.file_meta` 키가 들어가고 (MongoDB doc 의 `file_meta` 동작과 거울), 없으면 키 자체 부재. 옛 SDK 사용 시 `RequestMeta` 가 모르는 키는 Pydantic v2 기본 `extra="ignore"` 로 폐기 — 자연 호환.

## done_check

MDB 캐시 존재 여부만 확인한다. LLM을 호출하지 않는다.
캐시 키 생성 규칙은 [8. Runner — 캐시 키 생성](#8-runner) 참고.

```python
from llm_request import done_check, FileKeyingMeta

dc = await done_check(
    {"contents": "대한민국의 수도는?", "model": "gemini-2.5-flash"},
    files=[FileKeyingMeta(file_name="report.pdf", file_size=1024, file_mtime=1700000000)],
)

print(dc["cache_key"])    # SHA-256 캐시 키
print(dc["cache_hit"])    # True / False
print(dc["cached"])       # 캐시 문서 dict 또는 None
```

## fetch_by_cache_key

`done_check`으로 확보한 `cache_key`를 사용해 LLM 재호출 없이 캐시 결과를 조회한다.

```python
from llm_request import fetch_by_cache_key

response, meta = await fetch_by_cache_key(cache_key)
print(response.text)
print(meta.source)   # "cache"
```

# 7. File Handler

---

파일이 `llm_request_async`에 전달되면 내부(`_internal/file_processing`)에서 아래 순서로 처리된다.

```
StoredFile 리스트 (file_path + 5 메타)
  ↓
① 유효성 검사 (validators) — placeholder FileInput(data=b"") 으로 검증
  ↓
② 파일 분류 (resolver.classify_files)
     inline_pool / files_api_pool 분리
  ↓
③ 처리 방식 결정 (resolver.resolve_files) — bytes 가 필요한 시점에 file-manager 에서 lazy fetch
     inline       → fetch → bytes 그대로 전달 (standby 없음, 동시 N 요청 시 N 회 fetch)
     files_api    → URI 캐시 확인 → 없으면 standby leader 1 명만 fetch + 업로드
     context_cache_ttl 지정 시 → Context Cache 생성
```

## 유효성 검사

진입 즉시 fail-fast로 차단. 아래 두 조건 중 하나라도 위반하면 에러 반환.

**지원 MIME 타입 화이트리스트**

| 분류 | MIME 타입 |
| --- | --- |
| 이미지 | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/heif` |
| 오디오 | `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac` |
| 문서 | `application/pdf`, `text/plain`, `text/html`, `text/csv`, `text/markdown`, `text/xml`, `application/json`, `application/xml` |
| 비디오 | `video/mp4`, `video/mpeg`, `video/mov`, `video/avi`, `video/x-flv`, `video/webm`, `video/3gpp` |

**크기 제한**: 단일 파일 **2GB** 초과 시 거부 (inline·Files API 어느 경로로도 처리 불가)

## 파일 분류 전략

| 조건 | 처리 방식 |
| --- | --- |
| 비디오 | 항상 **Files API** (inline 불가) |
| inline 풀 합계 ≤ 20MB (이미지·오디오 포함 시) | **inline** |
| inline 풀 합계 ≤ 50MB (PDF·텍스트만) | **inline** |
| 위 한도 초과 | **Files API** |

이미지나 오디오가 하나라도 섞이면 inline 합계 한도가 20MB로 낮아진다. 작은 파일부터 한도 내에 채우고 초과분은 Files API로 넘긴다.

> PDF가 Files API 경로이고 **50MB 초과**이면 자동 분할 후 업로드 (→ PDF 자동 분할 참고)

## Files API URI 캐시 (Redis)

같은 파일을 매번 다시 업로드하지 않도록 URI를 Redis에 캐싱한다.

- **키**: `file_cache:files_api:{checksum}` (checksum = SHA-256(file_name + mtime + size))
- **TTL**: Gemini가 반환한 만료 시각 기준으로 동적 계산 (1시간 버퍼 차감). 계산 실패 시 **47시간** 폴백 (Gemini Files API 보존 기간 48시간보다 1시간 짧게 설정)
- 업로드 전 Redis 조회 → HIT이면 업로드 생략

## PDF 자동 분할

PDF가 50MB를 초과하면 자동으로 분할 후 병렬 업로드한다.

1. 페이지별 예상 크기 계산 (이미지 xref + 텍스트 콘텐츠)
2. 50MB 이하가 되도록 페이지 그룹핑
3. 그룹별 저장 → 크기 초과 시 재귀 재분할 (최대 3단계)
4. 인접 파트를 45MB 상한 내에서 병합

## Context Cache (opt-in)

`timings={"context_cache_ttl": 3600}` 을 지정할 때만 활성화된다. **지정하지 않으면 Context Cache는 생성되지 않는다.**

같은 **파일 조합 + 모델 + 시스템 프롬프트 + tools + tool_config** 가 반복될 때 입력 토큰을 최대 90% 절감한다.

- **캐시 키**: SHA-256(file_checksums + model + system_instruction + tools + tool_config)
- **Redis TTL**: 기본 1시간

> **Context Cache는 시간 단위로 비용이 발생한다.** 필요 이상으로 긴 TTL은 불필요한 비용을 유발하므로 실제 사용 패턴에 맞게 설정할 것.

> **캐시 키 재료(5개) 중 하나라도 달라지면 캐시 미스가 발생한다.** Context Cache는 `file_checksums` + `model` + `system_instruction` + `tools` + `tool_config` 를 하나의 키로 묶으므로, 이 중 하나라도 바뀌면 새로운 캐시가 생성된다. 특히 `tools` / `tool_config` 는 cache 생성 시 Gemini 쪽에도 함께 저장되므로, 같은 파일로도 tools 가 다르면 별도 cache 가 만들어진다 (오염된 cache HIT 방지).

### Context Cache 수명 단축 — `delete_cache_async` / `delete_cache`

`context_cache_ttl` 을 길게 잡아두고, agent task 가 끝났을 때 즉시 단축할 수 있다. storage 비용은 새 TTL 기준으로 분 단위 prorate 청구되므로, "넉넉히 잡고 끝나면 줄이기" 패턴이 비용 효율적이다.

```python
from llm_request import llm_request_async, delete_cache_async, StoredFile

cache_ids: set[str] = set()
for q in questions:
    response, meta = await llm_request_async(
        {"contents": q, "model": "gemini-2.5-flash"},
        stored_files=[StoredFile(**sf_dict)],
        timings={"context_cache_ttl": 3600},
    )
    if meta.context_cache_id:
        cache_ids.add(meta.context_cache_id)

# task 끝나면 일괄 삭제 (sync 래퍼 delete_cache 도 있음)
result = await delete_cache_async(list(cache_ids))
print(result.expired)   # ["..."]
print(result.failed)    # []
```

| 인자 | 기본값 | 설명 |
| --- | --- | --- |
| `cache_ids` | — | `meta.context_cache_id` 로 받은 식별자. 단건이면 문자열, 여러 개면 리스트 |
| `grace_seconds` | `5` | in-flight 요청 보호 grace (1~3600). 이 시간 후 Gemini cache + Redis 동시 만료 |

> ⚠️ 기본 `5`초는 **task 종료 직후 호출**을 가정한다. 같은 cache 를 쓰는 LLM 호출이 아직 진행 중일 수 있으면(대용량 컨텍스트 등 느린 호출) `grace_seconds` 를 넉넉히(예: 30~60) 올려 잡아 호출 완료 전에 만료되지 않게 한다.

> ⚠️ `meta.context_cache_id` 는 **live 호출일 때만** 응답에 들어간다. MDB cache HIT 응답에는 키 자체 부재. 첫 호출이 cache HIT 이면 그 task 의 cache_id 는 받지 못하지만, Gemini 측 cache 는 자연 만료되므로 큰 문제 없음.

> ⚠️ `context_cache_id` 는 **영구 식별자가 아니다.** Gemini 측 TTL 이 만료되면 cache 가 자동 파괴되고, 같은 입력으로 다시 호출하면 새 cache (새 id) 가 생성된다. agent 가 옛 id 를 DB 에 저장해두고 나중에 `delete_cache_async` 로 호출해도 이미 만료된 cache 라면 `expired` 분류로 idempotent 처리되니 안전 — 다만 옛 id 가 살아있다고 가정한 동작은 만들지 말 것.

> ⚠️ **expire 가 필요한 워크플로는 live 호출 직후의 `meta.context_cache_id` 를 보관할 것.** MDB cache HIT 응답에서는 노출되지 않으므로, 같은 입력으로 재호출하면 ID 를 받을 수 없어 그 시점에 expire 가 불가능하다 (옛 cache 가 살아있어도 호출자 측에서 ID 모르는 상태).

## Cache Stampede Dedup

동일 파일을 동시에 여러 요청이 업로드하려 할 때 중복 업로드를 방지한다.

- Redis SETNX로 선점 (`file_cache:files_api_standby:{checksum}`)
- 선점 실패한 요청은 exponential backoff으로 대기 (0.5초 → 1 → 2 → 4 → 최대 10초)
- 대기 중 캐시 HIT 확인 → HIT 시 즉시 반환
- `standby_ttl` 초과 시 대기 포기 후 직접 업로드 (중복 허용, 회복력 우선)


# 8. Runner

---

LLM 요청의 전체 실행 흐름. 캐시 키 생성 → 캐시 조회 → LLM 호출 → 저장 순으로 진행된다.

```
llm_input + files
  ↓
① 캐시 키 생성 (keying)
     file_checksums 주입 → subset 추출 → 정규화 → SHA-256
  ↓
② 캐시 조회 (done_check → MongoDB)
     HIT  → 즉시 반환
     MISS ↓
③ 파일 처리 (file_processing)
  ↓
④ LLM 호출 (llm_handler.aio.call)
  ↓
⑤ 결과 저장 (store → MongoDB)
  ↓
⑥ 반환
```

## 캐시 키 생성

### 1. 파일 checksum 주입

파일이 있으면 각 파일의 `(file_name, file_size, file_mtime)` 3개 필드로 SHA-256 checksum을 계산한 후 `file_checksums` 키로 `llm_input`에 주입한다. 이후 캐시 키는 텍스트 입력 + 파일 조합 전체를 반영한다.

> 파일이 있으면 `cached_content`는 volatile로 처리되어 캐시 키에서 제외된다.

### 2. subset 추출

아래 16개 필드만 캐시 키 계산에 사용된다. 목록에 없는 필드는 무시된다.

| 필드 | 설명 |
|------|------|
| `contents` | 필수. 질문/프롬프트 |
| `system` | 시스템 프롬프트 |
| `history` | 이전 대화 이력 |
| `model` | 모델명 |
| `max_tokens` | 최대 출력 토큰 수 |
| `temperature` | 온도 |
| `cached_content` | Context Cache 이름 (파일 있으면 제외) |
| `service_name` | 서비스 식별자 |
| `seed` | 재현성 시드값 |
| `output` | 출력 형식 |
| `schema` | 출력 스키마 |
| `thinking` | 사고 수준 |
| `tools` | 도구 정의 |
| `tool_config` | 도구 설정 |
| `media_resolution` | 미디어 해상도 |
| `file_checksums` | 파일 checksum 리스트 (파일 있을 때만) |

### 3. subset 전처리

`build_llm_input_subset()`에서 위 필드를 추출. 값이 `None`이거나 키가 없으면 `""`로 치환.

```python
llm_input = {"contents": "안녕", "temperature": None}

# 결과 subset:
{
    "contents": "안녕",   # 값 있음 → 그대로
    "temperature": "",    # None → "" 치환
    "model": "",          # 키 없음 → "" 치환
    ...                   # 나머지 필드도 모두 "" 포함
}
```

### 4. 정규화 (canonicalize)

`canonicalize_llm_input()`에서 각 필드 값을 재귀적으로 정규화한다.

| 타입 | 처리 방식 |
|------|-----------|
| `dict` | 키 문자열 변환, `None` → `""`, volatile 키 제거 |
| `list` / `tuple` | 각 원소 재귀 정규화. **순서 유지** (contents·history는 순서가 의미 있음) |
| `float` | 소수점 4자리 반올림 (`0.30000001` → `0.3`) |
| `Decimal` | `normalize()` 후 문자열 변환 (`Decimal("1.200")` → `"1.2"`) |
| `datetime` / `date` | `isoformat()` 변환 |
| `bytes` / `bytearray` | hex 문자열 변환 (`b'\xff\x00'` → `"ff00"`) |
| `str` / `int` / `bool` / `None` | 그대로 사용 |

**volatile 키**: 요청마다 달라지는 메타데이터. 포함하면 같은 내용의 요청도 다른 캐시 키가 된다.

| volatile 키 | 성격 |
|-------------|------|
| `timestamp`, `ts`, `created_at`, `updated_at` | 시간 정보 |
| `request_id`, `trace_id` | 요청 추적용 고유 ID |

### 5. JSON 직렬화 → SHA-256

- dict 키 알파벳 정렬
- 구분자 공백 제거 (값 내부 공백은 유지)
- UTF-8 인코딩 후 SHA-256 → **64자리 hex 캐시 키**

```python
# 값 안의 공백은 유지되므로 아래 둘은 다른 캐시 키:
"안녕하세요"   → 해시 A
"안녕하세요 "  → 해시 B  (뒤에 공백 하나)
```

## 캐시 조회 및 LLM 호출 흐름

`cache_mode` / `save_mode` 조합에 따라 경로가 달라진다.

| cache_mode | save_mode | 흐름 |
| --- | --- | --- |
| `False` | `False` | 키 생성 없음 → LLM 직접 호출 → 저장 없음 |
| `False` | `True` | 키 생성 → LLM 호출 → MDB 저장 |
| `True` | `True` | 키 생성 → MDB 조회 → HIT 반환 / MISS LLM 호출 + 저장 |
| `True` | `False` | 키 생성 → MDB 조회 → HIT 반환 / MISS LLM 호출만 |

## MDB 저장 구조

상세 스키마는 [10. MDB 저장 구조](#10-mdb-저장-구조) 참고.


# 9. (구) Service API — 제거됨

---

> ⚠️ **0.3.0 에서 FastAPI 서버(port 8101)와 아래 HTTP 엔드포인트는 전부 제거됐다.**
> 이제 SDK 공개 함수(§6)가 같은 프로세스에서 manager 로직을 직접 호출한다. HTTP 요청 경로는 없다.
> 아래 요청/응답 표는 **각 공개 함수가 받고 돌려주는 데이터 형태의 참고**로만 남겨둔다
> (`/v1/request` ≈ `llm_request_async`, `/v1/done-check` ≈ `done_check`, `/v1/fetch` ≈ `fetch_by_cache_key`).

## (구) POST /v1/request

LLM 호출 + 캐시 조회/저장. SDK `llm_request_async`의 백엔드.

**요청 바디**

| 필드 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | — | llm-handler에 전달할 입력 |
| `stored_files` | `list[StoredFile] \| null` | `null` | file-manager 등록 후 받은 6 필드 리스트. Service 가 `file_path` 로 bytes 를 lazy fetch |
| `cache_mode` | `bool` | `true` | MDB 캐시 조회 여부 |
| `save_mode` | `bool` | `true` | MDB 저장 여부 |
| `ttl_seconds` | `int \| null` | `null` | MDB 캐시 수명 (현재 미동작) |
| `database` | `str \| null` | `null` | MDB database 이름 (기본 `kw_ai`) |
| `context_cache_ttl` | `int \| null` | `null` | Gemini Context Cache 수명 (초). `null` = 생성 안 함 |
| `standby_ttl_seconds` | `int` | `600` | Cache stampede dedup 대기 timeout (초) |
| `debug` | `bool` | `false` | `true`면 응답 `meta.route_reason`에 처리 경로 포함 |

**StoredFile 구조** (6 필드)

| 필드 | 설명 |
| --- | --- |
| `file_path` | file-manager 가 부여한 저장 경로 (`register_or_get` 결과) |
| `file_name` | 파일 이름 |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 수정 시각 (unix timestamp) |
| `mime_type` | MIME 타입 |
| `extension` | 확장자 |

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

## POST /v1/request/stream

스트리밍 LLM 요청. SSE(Server-Sent Events) 형식으로 응답. 요청 바디는 `/v1/request`와 동일.

**응답 (SSE)**

```
data: {"type": "chunk", "text": "...", "finish_reason": null}
data: {"type": "chunk", "text": "...", "finish_reason": "STOP", "usage": {...}}
data: [DONE]
```

## POST /v1/done-check

MDB에 해당 입력의 응답이 이미 저장되어 있는지 확인. LLM 호출 없음.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `llm_input` | `dict` | 캐시 키 계산 대상 입력 |
| `files` | `list[FileKeyingMeta] \| null` | 파일 메타 (file_name, file_size, file_mtime). bytes 불필요 |
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

## POST /v1/fetch

캐시 키를 직접 지정해 MDB에서 결과 조회.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `cache_key` | `str` | 조회할 SHA-256 캐시 키 |
| `database` | `str \| null` | MDB database |

**응답**: `/v1/request`와 동일한 `result` + `meta` 구조.


# 10. MDB 저장 구조

---

## Database / Collection

| 설정 | 값 |
| --- | --- |
| 기본 database | `kw_ai` (`MONGODB_DATABASE` env 또는 `database` 파라미터로 변경) |
| 기본 collection | `agent_llm` |

테스트/검증 시 `kw_ai_dev`를 사용한다 (`database="kw_ai_dev"`).

## 문서 스키마

`_id`는 SHA-256 캐시 키(64자 hex). 같은 키로 재저장 시 upsert(전체 교체).

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `_id` | `str` | SHA-256 캐시 키 |
| `canonical_json` | `str` | 캐시 키 계산에 쓰인 정규화 JSON |
| `result` | `dict` | `LLMResponse.model_dump()` |
| `metadata` | `dict` | `{"service_name": ..., "model": ...}` (`llm_input`에서 추출) |
| `created_at` | `str` | 저장 시각 (UTC ISO) |
| `requested_at` | `str` | LLM 요청 시각 (UTC ISO, LLM 호출 시에만) |
| `expires_at` | `str` | `mdb_ttl` 설정 시에만. **현재 필드만 기록, 자동 삭제 미동작** |
| `file_meta` | `list[dict]` | `stored_files` 포함 요청 시. 각 `StoredFile` 의 6 필드(`file_path` 포함) 리스트 |

## TTL 현황

`mdb_ttl` / `ttl_seconds` 지정 시 `expires_at` 필드가 기록된다.
MongoDB TTL 인덱스가 없어 자동 삭제는 동작하지 않으며, 현재 모든 문서는 영구 저장 상태다.


# 11. 에러 처리

---

## 공개 예외 계층

서버가 제거되어 manager 로직의 예외가 호출자에게 **직접** 전파된다. 모두 `LLMRequestServiceError` 를 상속하며 `.error_type` / `.status_code` 속성을 갖는다 (속성은 분기/로깅용으로 유지).

| 예외 | `error_type` | `status_code` | 발생 조건 |
| --- | --- | --- | --- |
| `ValidationError` | `validation` | 422 | 입력 검증 실패 |
| `UnsupportedFileTypeError` | `unsupported_file_type` | 422 | Gemini 지원 외 MIME 타입 |
| `FileTooLargeError` | `file_too_large` | 422 | 개별 파일 2GB 초과 |
| `FileUploadError` | `file` | 502 | Files API 업로드 실패 |
| `FileResolveError` | `file_resolve` | 502 | stored_files resolve 실패 (file-manager에서 파일 못 찾음) |
| `CacheError` | `cache` | 502 | MDB / Redis 캐시 조회·저장 실패 |
| `CacheKeyNotFoundError` | `not_found` | 404 | `fetch_by_cache_key` 대상 캐시 문서 없음 |
| `LLMRequestServiceError` | `handler` | 500 | 베이스 / 그 외 fallback |

LLM 호출 실패는 llm-handler 의 `HandlerError` / `HandlerConnectionError` 가 그대로 전파된다 (re-export 됨).

```python
from llm_request import (
    llm_request_async, CacheError, FileResolveError,
    LLMRequestServiceError, HandlerError, HandlerConnectionError,
)

try:
    response, meta = await llm_request_async(llm_input)
except FileResolveError:
    ...                       # 파일 재등록 후 재호출
except (HandlerError, HandlerConnectionError) as e:
    ...                       # LLM 호출 자체 실패
except LLMRequestServiceError as e:
    print(e.error_type, e.status_code, e.message)
```

> **오류 발생 시**: `[route]` / `[dedup]` 로그 패턴, `route_reason` 값(`debug=True`) 으로 처리 경로를 추적할 수 있다.

## LLM 오류는 MDB에 저장되지 않음

LLM 호출 중 예외가 발생하면 저장 단계로 진입하지 않아 캐시 문서가 생성되지 않는다.


# 12. 마이그레이션

---

## v0.2.0 → v0.3.0 (서버 제거 → 순수 SDK)

| 항목 | v0.2.0 | v0.3.0 |
| --- | --- | --- |
| 설치 경로 | `packages/llm-request/sdk` | **`packages/llm-request`** (루트가 곧 패키지) |
| Service 실행 | FastAPI(8101) 필수 | **불필요 — 서버 제거** |
| 환경변수 | `LLM_REQUEST_URL` | 불필요 (handler URL 하드코딩, 필요 시 개별 override) |
| 예외 | `ServiceError` / `ServiceConnectionError` | 구체 예외(`FileResolveError`·`CacheError` 등) + `HandlerError` 직접 전파 ([11. 에러 처리](#11-에러-처리)) |
| `close()` | httpx 풀 정리 | no-op (그대로 둬도 안전) |
| **공개 API 시그니처·호출** | — | **불변** (`llm_request_async`, `stored_files`, `meta.*` 그대로) |

> 핵심: **호출 코드는 거의 그대로**다. 바뀌는 건 (1) 설치 경로, (2) 서버 안 띄움, (3) `except ServiceError` 쓰던 곳을 구체 예외로 교체. `ServiceError` 를 import 하던 코드만 수정 필요.

## v0.1.0 → v0.2.0 (이력)

## 변경 요약

| 항목 | v0.1.0 | v0.2.0 |
| --- | --- | --- |
| 설치 경로 | `packages/llm-request` | `packages/llm-request/sdk` |
| ⚠️ Service 실행 | 불필요 | **별도 FastAPI Service (port 8101) 실행 필수** |
| 환경변수 | `MDB_HANDLER_URL`, `LLM_HANDLER_URL`, `MONGODB_DATABASE` 등 여러 개 | `LLM_REQUEST_URL` 하나 (기본값 내장) |
| 초기화 코드 | `llm_handler.configure()`, `MongoDBHandler.reset()` 필요 | 불필요 |
| ⚠️ 파일 입력 | `file_paths=["report.pdf"]` | `stored_files=[StoredFile(...)]` (file-manager `register_or_get` 결과) |
| ⚠️ meta 반환 타입 | `dict` (`meta["source"]`) | `RequestMeta` (`meta.source`) |

> **⚠️ 설치 경로 변경 주의**
>
> v0.1.0 에선 `pip install -e packages/llm-request` 로 설치했지만, v0.2.0 에선 이 경로가 **uv workspace 루트** 로 바뀌었다. 같은 명령을 쓰면 **빈 워크스페이스만 설치**되어 SDK 기능이 없는 상태가 된다. 새 경로로 설치해야 한다.
>
> ```bash
> pip install -e packages/llm-request/sdk    # ← sdk 하위 경로 사용
> # 또는 루트에서 전체 워크스페이스 설치:
> cd packages/llm-request && uv sync
> ```
>
> 기존 v0.1.0 환경에서 업그레이드할 경우 먼저 `pip uninstall kiwoom-llm-request` 로 구 버전을 제거한 뒤 새 경로로 설치 권장.

## 초기화 코드 제거

```python
# v0.1.0 — 이 블록 전체 제거 가능
import llm_handler
llm_handler.configure()
llm_handler.aio.configure()

from mdb_handler import MongoDBHandler
MongoDBHandler.reset()

os.environ["MDB_HANDLER_URL"] = "http://192.7.9.45:8300"
os.environ["MONGODB_DATABASE"] = "kw_ai"

# v0.2.0 — 필요한 경우만
os.environ["LLM_REQUEST_URL"] = "http://192.7.9.45:8101"  # 기본값, 생략 가능
```

## ⚠️ 파일 입력 변경

```python
# v0.1.0
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)

# v0.2.0
from llm_request import llm_request_async, StoredFile
from file_manager import FileManagerHandler

fm = FileManagerHandler()
sf_dict = await fm.register_or_get(
    bytes_data=open("report.pdf", "rb").read(),
    metadata=fm.build_metadata("report.pdf"),
)
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    stored_files=[StoredFile(**sf_dict)],
)
```

## ⚠️ meta 접근 방식 변경

```python
# v0.1.0 — dict 접근
print(meta["source"])
print(meta["cache_hit"])

# v0.2.0 — 속성 접근
print(meta.source)
print(meta.cache_hit)
```


# 13. 주의사항

---

### 스트림은 이터레이터를 끝까지 소비해야 저장됨

`llm_request_stream` / `llm_request_stream_async`는 모든 청크를 순회한 뒤 MDB에 저장한다.
중간에 `break`하거나 이터레이터를 버리면 저장이 실행되지 않는다. 두 함수는 `(stream, meta)` 튜플을 반환한다 — meta 는 스트림 시작 전 바로 확인 가능 (`cache_hit`, `cache_key` 등).

```python
stream, meta = await llm_request_stream_async(...)
print(meta["cache_hit"])            # 스트림 시작 전 확인 가능
async for chunk in stream:
    if some_condition:
        break  # ⚠️ 저장 안 됨
```

---

### `ttl_seconds`는 `expires_at` 필드만 기록 — 자동 삭제는 MongoDB TTL 인덱스 필요

`ttl_seconds` 지정 시 문서에 `expires_at` 필드만 기록된다.
자동 삭제를 원하면 MDB 측에 TTL 인덱스(`expires_at` 대상)가 설정되어 있어야 한다.

---

### 파일명도 캐시 키에 영향

파일의 캐시 키는 `file_name + file_mtime + file_size` 체크섬 기반이다.
세 값 중 하나라도 다르면 다른 체크섬 → 다른 캐시 키가 된다.
같은 내용의 파일이라도 다른 이름으로 다시 보내면 cache miss.

---

### 브라우저 업로드 파일은 mtime 추출에 주의

로컬 경로가 없는 경우(프론트엔드에서 업로드되는 에이전트 등) Python `Path.stat().st_mtime`을 쓸 수 없으므로, **프론트엔드에서 `File.lastModified` 값을 백엔드로 함께 전달**해야 한다. JavaScript `File` 객체의 `lastModified`는 밀리초 단위이므로 초 단위로 변환해서 넘긴다.

```js
// frontend (JS)
const mtime = Math.floor(file.lastModified / 1000);
// 업로드 폼 / JSON 등에 file_mtime 필드로 같이 전달
```

서버 수신 시점의 현재 시각(`time.time()` 등)을 `file_mtime`으로 대신 쓰면, 동일 파일을 다시 업로드할 때마다 `file_mtime`이 달라져 **cache miss가 반복**된다.

---

### 동시 요청 시 `standby_ttl_seconds` 조정

같은 입력의 동시 요청은 Redis standby 메커니즘으로 중복 LLM 호출을 방지한다.

- 첫 요청이 `standby_ttl_seconds` 내에 완료되면 후속 요청은 자동으로 캐시 결과를 사용
- 시간 내 완료되지 않으면 후속 요청이 별도 LLM 호출 시작 (두 응답이 다를 수 있음)
- 작업 소요 시간에 맞게 조정 권장: 짧은 작업 120~300초 / 대용량 + 무거운 모델 900~1200초

---

### 개발/테스트 시 `kw_ai_dev` 사용

```python
# 저장 없이 LLM만 호출
response, meta = await llm_request_async(
    {"contents": "테스트", "model": "gemini-2.5-flash"},
    cache_mode=False,
    save_mode=False,
)

# 또는 dev DB에 저장
response, meta = await llm_request_async(..., database="kw_ai_dev")
```

---

### `close()` 는 이제 no-op

0.2.0 까지는 SDK 가 httpx `AsyncClient` 풀을 소유해 `close()` 로 정리해야 했다. 서버 제거(0.3.0)로 SDK 가 소유한 커넥션이 없어져 **`close()` 는 하위호환 no-op** 이다. 기존 코드의 `await close()` 호출은 그대로 둬도 안전(무동작)하다. handler 호출의 커넥션은 각 handler SDK 가 자체 관리한다.


# 14. 향후 계획

---

## Files API 20GB 한도 관리

Gemini Files API는 계정당 20GB 저장 한도가 있다.
현재는 한도 초과 시 별도 처리 없이 업로드 오류로 전파된다.
`llm-handler`에 `delete_file()` SDK가 노출되면 한도 초과 시 오래된 파일 자동 삭제 로직을 구현할 예정.

## 에이전트별 균등 호출 조절

여러 에이전트가 동시에 실행될 때 특정 에이전트가 LLM 호출을 독점하지 않도록
에이전트 단위의 quota / rate limit을 두는 방식을 검토 중이다.
호출량 제어는 공용 서버인 `llm-handler` 레이어의 책임이며, 거기서 동시 호출 수 제한을 두는 방향이다.





# 15. llm_handler re-export

---

`llm_request` 패키지만 설치하면 `llm_handler`의 모든 public API를 별도 import 없이 사용할 수 있다.

## 방법 1: flat import (sync)

```python
from llm_request import call, stream, embed, configure, Part, LLMResponse

# 초기화 (기본값 내장, URL이 다를 때만 필요)
configure(base_url="http://192.7.9.45:8100")

# 동기 호출
response = call("안녕하세요")
print(response.text)

# 스트리밍
for chunk in stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)

# 임베딩
result = embed("텍스트", model="text-embedding-004")
```

### re-export 목록

| 분류 | 이름 |
| --- | --- |
| 호출 | `call`, `stream`, `embed`, `session` |
| 파일 | `upload_file`, `delete_file`, `get_file`, `list_files` |
| Context Cache | `create_cache`, `delete_cache`, `get_cache`, `update_cache`, `list_caches` |
| 설정 | `configure` |
| 클래스 | `Session` |
| 모델 | `Part`, `LLMResponse`, `StreamChunk`, `FunctionCall`, `CacheRef`, `FileRef`, `TokenUsage`, `ContentsType` |
| 임베딩 모델 | `EmbedResponse`, `EmbeddingStatistics`, `EmbeddingValue` |
| 예외 | `SDKError`, `HandlerConnectionError`, `HandlerError` |

## 방법 2: aio 서브모듈 (async)

```python
from llm_request import aio

# 초기화
aio.configure(base_url="http://192.7.9.45:8100")

# 비동기 호출
response = await aio.call("안녕하세요")

# 비동기 스트리밍
async for chunk in aio.stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)

# 비동기 임베딩
result = await aio.embed("텍스트")
```

`aio` 서브모듈은 `llm_handler.aio`를 그대로 re-export한다.
`AsyncSession`, `configure`, `upload_file`, `create_cache` 등 sync와 동일한 구성.

## 방법 3: 모듈 접근

```python
from llm_request import llm_handler

# sync
response = llm_handler.call("안녕하세요")

# async (llm_handler.aio 접근 가능)
response = await llm_handler.aio.call("안녕하세요")

# 설정
llm_handler.configure(base_url="http://192.7.9.45:8100")
llm_handler.aio.configure(base_url="http://192.7.9.45:8100")
```
