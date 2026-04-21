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
| 버전 | `0.2.0` |
| Python | **`>=3.11`** |
| 구성 | **SDK** (에이전트 설치) + **Service** (FastAPI, port 8101) |
| SDK 의존성 | `httpx`, `filetype`, `kiwoom-redis-handler`, `kiwoom-file-manager` |
| Service 의존성 | `kiwoom-llm-handler`, `kiwoom-mdb-handler`, `kiwoom-redis-handler`, `kiwoom-file-manager`, `PyMuPDF` |
| 통신 방식 | SDK → **HTTP(httpx)** → Service(8101). MDB·Redis·LLM 호출은 Service가 일괄 담당 |

# 3. 디렉터리 구조

```
packages/llm-request/
├── sdk/
│   ├── llm_request/
│   │   ├── __init__.py        # 공개 API (llm_request_async)
│   │   ├── _client.py         # HTTP 클라이언트 (httpx)
│   │   ├── aio.py             # 비동기 헬퍼
│   │   ├── models.py          # FileInput, Timings TypedDict
│   │   └── exceptions.py      # SDK 예외
│   └── pyproject.toml
├── service/
│   ├── app/
│   │   ├── main.py            # FastAPI 앱 진입점 (port 8101)
│   │   ├── config.py          # 환경변수 설정
│   │   ├── core/
│   │   │   ├── errors.py      # 예외 핸들러
│   │   │   └── providers.py   # 의존성 주입 (MDB, Redis, LLM)
│   │   ├── routers/
│   │   │   └── request.py     # POST /request 엔드포인트
│   │   ├── models/
│   │   │   ├── requests.py    # 요청 스키마
│   │   │   └── responses.py   # 응답 스키마
│   │   └── logic/
│   │       ├── runner.py       # LLM 호출
│   │       ├── keying.py       # 캐시 키 생성
│   │       ├── done_check.py   # 캐시 조회
│   │       ├── store.py        # MongoDB 캐시 저장
│   │       ├── meta.py         # 저장용 메타 생성
│   │       ├── types.py        # 내부 타입 정의
│   │       ├── file_register.py       # file-manager에 파일 등록 (Service 진입 시점)
│   │       └── file_processing/
│   │           ├── resolver.py    # 파일 분류 (inline / Files API / context cache)
│   │           ├── uploader.py    # Files API 업로드
│   │           ├── cache_store.py # URI 캐시 (Redis)
│   │           ├── dedup.py       # Cache stampede dedup
│   │           ├── splitter.py    # PDF 분할
│   │           └── validators.py  # 파일 유효성 검사
│   └── pyproject.toml
└── quickstarts/
    ├── quickstart_sdk.ipynb
    └── verify_all.ipynb
```


# 4. 설정 및 준비사항

## SDK 설치

```bash
pip install -e ./packages/llm-request/sdk
```

SDK는 `LLM_REQUEST_URL` 하나만 보고 Service에 연결한다.

```python
import os
os.environ["LLM_REQUEST_URL"] = "http://192.7.9.45:8101"  # 기본값, 생략 가능
```

## Service 환경변수

Service는 `.env` 또는 환경변수로 설정한다. 기본값이 내부 서버 주소이므로 그대로 두면 된다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_HANDLER_URL` | `http://192.7.9.45:8100` | LLM Handler 서비스 |
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | MDB Handler 서비스 |
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | Redis |
| `FILE_MANAGER_URL` | `http://192.7.9.45:8600` | file-manager 서비스 |
| `FILE_MANAGER_TIMEOUT` | `30.0` | file-manager 요청 타임아웃 (초) |
| `MONGODB_DATABASE` | `kw_ai` | MongoDB 데이터베이스명 |
| `PORT` | `8101` | Service 포트 |

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

## 파일 첨부

파일을 첨부할 때는 에이전트가 직접 `FileInput`을 구성해서 전달한다. `FileInput`은 아래 5개 필드로 이루어진다.

| 필드 | 설명 |
| --- | --- |
| `file_name` | 파일명 (확장자 포함) |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 파일 수정 시각 (Unix timestamp, int) |
| `mime_type` | MIME 타입 (예: `application/pdf`) |
| `extension` | 확장자 (예: `pdf`, 점 없이) |
| `data` | 파일 바이트 (`bytes`) |

> **⚠️ file_name, file_size, file_mtime은 실제 파일에서 추출한 값과 반드시 일치해야 한다.**
> 이 세 값은 캐시 키 생성에 직접 사용된다. 잘못된 값을 넣으면 캐시 미스가 반복 발생한다.

파일이 여러 개인 경우 리스트에 담아서 전달한다.

```python
from pathlib import Path
import mimetypes
from llm_request import llm_request_async, FileInput

def make_file_input(p: Path) -> FileInput:
    return FileInput(
        file_name=p.name,
        file_size=p.stat().st_size,
        file_mtime=int(p.stat().st_mtime),
        mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        extension=p.suffix.lstrip("."),
        data=p.read_bytes(),
    )

files = [make_file_input(Path("report.pdf")), make_file_input(Path("data.csv"))]

response, meta = await llm_request_async(
    {"contents": "두 파일을 비교 분석해줘", "model": "gemini-2.5-flash"},
    files=files,
)
print(response.text)
print(meta.source)
```


# 6. SDK

---

## 공개 API

| 함수 | 반환 타입 | 설명 |
| --- | --- | --- |
| `llm_request_async` | `tuple[LLMResponse, RequestMeta]` | 비동기 단건 LLM 호출 |
| `llm_request` | `tuple[LLMResponse, RequestMeta]` | 동기 단건 (`asyncio.run` 래퍼) |
| `llm_request_stream_async` | `AsyncIterator[dict]` | 비동기 스트리밍 |
| `llm_request_stream` | `Iterator[dict]` | 동기 스트리밍 |
| `done_check` | `DoneCheckResult` | 캐시 존재 여부만 확인 (LLM 호출 없음) |
| `fetch_by_cache_key` | `tuple[LLMResponse, RequestMeta]` | 캐시 키로 직접 조회 |

## llm_input 구조

`llm_request_async`의 첫 번째 인자. **`llm_handler.call()`의 파라미터 대부분과 동일하며, 파일 관련만 `contents`에서 빼고 별도의 `files=[FileInput]` 인자로 분리**한 점이 차이다. `llm_input`에는 텍스트 프롬프트와 생성 파라미터만 넣고, 파일은 `FileInput`으로 메타와 bytes를 구성해 `files=`로 넘기면 Service가 inline / Files API 분류·업로드·캐싱을 자동 처리한다.

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
        "media_resolution": "high",     # 선택 ("low" | "medium" | "high" | "ultra_high")
    },
    files=[...],           # 선택. list[FileInput]
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
    media_resolution: str | None = None, # "low" | "medium" | "high" | "ultra_high"
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
| `context_cache_ttl` | `None` | Gemini Context Cache 수명 (초) | **None이면 Context Cache 비활성. 반복 참조 파일에 Context Cache를 쓰려면 반드시 값을 설정해야 함** (예: `3600`) |
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

파일이 `llm_request_async`에 전달되면 Service 내부에서 아래 순서로 처리된다.

```
FileInput 리스트
  ↓
① 유효성 검사 (validators)
  ↓
② file-manager에 파일 등록
  ↓
③ 파일 분류 (resolver.classify_files)
     inline_pool / files_api_pool 분리
  ↓
④ 처리 방식 결정 (resolver.resolve_files)
     inline       → bytes 그대로 전달
     files_api    → URI 캐시 확인 → 없으면 업로드
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

같은 파일 조합 + 모델 + 시스템 프롬프트가 반복될 때 입력 토큰을 최대 90% 절감한다.

- **캐시 키**: SHA-256(file_checksums + model + system_instruction)
- **Redis TTL**: 기본 1시간

> **Context Cache는 시간 단위로 비용이 발생한다.** 필요 이상으로 긴 TTL은 불필요한 비용을 유발하므로 실제 사용 패턴에 맞게 설정할 것.

> **model 또는 system_instruction이 달라지면 캐시 미스가 발생한다.** Context Cache는 파일 조합 + 모델 + 시스템 프롬프트를 하나의 키로 묶으므로, 이 중 하나라도 바뀌면 새로운 캐시가 생성된다.

## Gemini 404 자동 복구

Gemini가 Files API URI를 404로 응답하면 해당 URI의 Redis 캐시를 무효화하고 자동으로 재업로드한다. 호출자는 별도 처리 없이 투명하게 복구된다.

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


# 9. Service API

---

FastAPI 서버 (port 8101). SDK가 내부적으로 호출하며, 직접 HTTP 요청도 가능하다.

## POST /v1/request

LLM 호출 + 캐시 조회/저장. SDK `llm_request_async`의 백엔드.

**요청 바디**

| 필드 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | — | llm-handler에 전달할 입력 |
| `files` | `list[FileInput] \| null` | `null` | 파일 리스트 (meta + bytes, base64) |
| `cache_mode` | `bool` | `true` | MDB 캐시 조회 여부 |
| `save_mode` | `bool` | `true` | MDB 저장 여부 |
| `ttl_seconds` | `int \| null` | `null` | MDB 캐시 수명 (현재 미동작) |
| `database` | `str \| null` | `null` | MDB database 이름 (기본 `kw_ai`) |
| `context_cache_ttl` | `int \| null` | `null` | Gemini Context Cache 수명 (초). `null` = 생성 안 함 |
| `standby_ttl_seconds` | `int` | `600` | Cache stampede dedup 대기 timeout (초) |
| `debug` | `bool` | `false` | `true`면 응답 `meta.route_reason`에 처리 경로 포함 |

**FileInput 구조**

| 필드 | 설명 |
| --- | --- |
| `file_name` | 파일 이름 |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 수정 시각 (unix timestamp) |
| `mime_type` | MIME 타입 |
| `extension` | 확장자 |
| `data` | 파일 바이너리 (base64 인코딩) |

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
| `file_meta` | `list[dict]` | 파일 포함 요청 시. `FileInput` meta 리스트 (data 제외) |

## TTL 현황

`mdb_ttl` / `ttl_seconds` 지정 시 `expires_at` 필드가 기록된다.
MongoDB TTL 인덱스가 없어 자동 삭제는 동작하지 않으며, 현재 모든 문서는 영구 저장 상태다.


# 11. 에러 처리

---

## SDK 예외 계층

| 예외 | 발생 조건 |
| --- | --- |
| `ServiceConnectionError` | Service 연결 실패 (네트워크 오류, 타임아웃) |
| `ServiceError` | Service가 4xx/5xx 에러 응답 반환. `error_type`으로 원인 구분 |

```python
from llm_request import llm_request_async, ServiceConnectionError, ServiceError

try:
    response, meta = await llm_request_async(llm_input)
except ServiceConnectionError:
    # llm-request 서비스 자체에 연결 못 함
    ...
except ServiceError as e:
    print(e.status_code, e.error_type, e.message)
```

## Service 에러 타입 (`error_type`)

| `error_type` | HTTP | 발생 조건 |
| --- | --- | --- |
| `validation` | 422 | 요청 스키마 검증 실패 |
| `unsupported_file_type` | 422 | Gemini 지원 외 MIME 타입 |
| `file_too_large` | 422 | 개별 파일 2GB 초과 |
| `file` | 502 | Files API 업로드 실패 |
| `cache` | 502 | MDB 캐시 조회/저장 실패 |
| `handler` | 500 | LLM 호출 또는 내부 오류 |

## 에러 응답 형식

```json
{
  "error": "에러 메시지",
  "detail": "상세 내용 (선택)",
  "error_type": "validation | cache | file | handler | ...",
  "request_id": "uuid"
}
```

`request_id`는 서버 로그와 대조하여 원인을 추적할 수 있다.

> **오류 발생 시**: 서버 로그 포맷, `[route]` / `[dedup]` 로그 패턴, `route_reason` 값 목록을 담은 디버깅 가이드 → [docs/14.observability-backlog.md](docs/14.observability-backlog.md)

## LLM 오류는 MDB에 저장되지 않음

LLM 호출 중 예외가 발생하면 저장 단계로 진입하지 않아 캐시 문서가 생성되지 않는다.
다음 요청 시 동일 입력으로 LLM을 다시 호출한다.


# 12. v0.1.0 → v0.2.0 마이그레이션

---

## 변경 요약

| 항목 | v0.1.0 | v0.2.0 |
| --- | --- | --- |
| 설치 경로 | `packages/llm-request` | `packages/llm-request/sdk` |
| ⚠️ Service 실행 | 불필요 | **별도 FastAPI Service (port 8101) 실행 필수** |
| 환경변수 | `MDB_HANDLER_URL`, `LLM_HANDLER_URL`, `MONGODB_DATABASE` 등 여러 개 | `LLM_REQUEST_URL` 하나 (기본값 내장) |
| 초기화 코드 | `llm_handler.configure()`, `MongoDBHandler.reset()` 필요 | 불필요 |
| ⚠️ 파일 입력 | `file_paths=["report.pdf"]` | `files=[FileInput(...)]` |
| ⚠️ meta 반환 타입 | `dict` (`meta["source"]`) | `RequestMeta` (`meta.source`) |

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
from pathlib import Path
import mimetypes
from llm_request import llm_request_async, FileInput

p = Path("report.pdf")
file_input = FileInput(
    file_name=p.name,
    file_size=p.stat().st_size,
    file_mtime=int(p.stat().st_mtime),
    mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
    extension=p.suffix.lstrip("."),
    data=p.read_bytes(),
)
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    files=[file_input],
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
중간에 `break`하거나 이터레이터를 버리면 저장이 실행되지 않는다.

```python
async for chunk in llm_request_stream_async(...):
    if some_condition:
        break  # ⚠️ 저장 안 됨
```

---

### `ttl_seconds`는 `expires_at` 필드만 기록 — 자동 삭제는 MongoDB TTL 인덱스 필요

`ttl_seconds` 지정 시 문서에 `expires_at` 필드만 기록된다.
자동 삭제를 원하면 MDB 측에 TTL 인덱스(`expires_at` 대상)가 설정되어 있어야 한다.

---

### inline 파일(≤20MB) 다수 첨부 시 요청 크기 주의

파일은 base64로 인코딩되어 HTTP JSON body에 포함된다.
base64 인코딩은 원본 대비 약 33% 크기가 증가하므로, 20MB 파일 5개를 동시에 보내면 요청 body가 약 135MB에 달할 수 있다.

---

### 파일명은 캐시 키에 영향 없음

파일의 캐시 키는 `file_name + file_mtime + file_size` 체크섬 기반이다.
같은 내용의 파일이면 이름이 달라도 캐시 히트가 발생한다.

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


# 14. 향후 계획

---

## Files API 20GB 한도 관리

Gemini Files API는 계정당 20GB 저장 한도가 있다.
현재는 한도 초과 시 별도 처리 없이 업로드 오류로 전파된다.
`llm-handler`에 `delete_file()` SDK가 노출되면 한도 초과 시 오래된 파일 자동 삭제 로직을 구현할 예정.

## 에이전트별 균등 호출 조절

여러 에이전트가 동시에 실행될 때 특정 에이전트가 LLM 호출을 독점하지 않도록
에이전트 단위의 quota / rate limit을 두는 방식을 검토 중이다.
Service 레이어에서 Semaphore 기반으로 동시 호출 수를 제한하여 에이전트 간 공정한 리소스 배분을 목표로 한다.





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
