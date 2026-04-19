# Pull Request — llm-request v0.1.0 → v0.2.0

# 📝 개요 (Overview)

`llm_request`를 단일 라이브러리에서 **SDK + Service(FastAPI, port 8101) 2-tier 구조**로 전환하고, 파일 입력 패러다임을 `FileInput(bytes)`로 통일하면서 **Files API URI 캐시 / Context Cache / Cache stampede dedup** 등 캐싱·동시성 인프라를 신설한다. 에이전트는 `LLM_REQUEST_URL` 하나와 `files=[FileInput]`만 알면 되고, MongoDB · Redis · llm-handler · file-manager 연결·관리는 Service가 전담한다.

13개 Task로 구성:
- **A. 변경된 로직 (Task 1 ~ 6)** — 아키텍처·SDK 파라미터·파일 입력·캐시 키·MDB 스키마·Redis 필수화
- **B. 새로 추가된 로직 (Task 7 ~ 13)** — Service 엔드포인트 / file-manager 등록 / Files API URI 캐시 + 404 복구 / Context Cache opt-in / Cache stampede dedup / Error 계층 / llm-handler 재수출 동기화
- **C. 참고사항** — file-manager 간이 구현 상태 등
- **D. Fix** — Issue #441 (async 재수출 누락 + configure 충돌 정리)

# 🔗 관련 이슈 (Related Issue)

- **closes #441** — `llm_handler` 재수출 목록에서 async 함수 누락 + `llm_handler` 직접 import 강제 (해결 상세: D. Fix 섹션)

# ✅ 변경 사항 (Changes)

## A. 변경된 로직

### Task 1. SDK + Service 분리 + 환경변수 일원화

#### 배경

기존 `llm_request`는 단일 라이브러리로 제공됐고, 에이전트가 이를 설치하면 그 하위에 `mdb_handler`, `redis_handler`, `llm_handler`, `file-manager` 등 여러 handler SDK가 함께 딸려 들어와 각각 초기화·환경변수 설정을 해야 했다. 에이전트가 늘어날수록 다음 문제가 커졌다.

- 에이전트 하위에 인프라 연결이 중첩돼 구조가 지저분함. 각 에이전트마다 `MongoDBHandler.reset()`·`llm_handler.configure()`·`llm_handler.aio.configure()` 같은 초기화 보일러플레이트를 반복 배치해야 함
- 동일 인프라를 여러 에이전트가 각자 붙드는 형태라 연결·리소스가 중복됨
- 차후 **에이전트별 호출 균일 배분(quota / rate limit)** 을 구현하려면 호출량을 중앙에서 통제할 수 있는 공통 Service가 필수

결국 SDK-only 구조의 지저분함 해소와 차후 균일 배분을 위한 토대 마련이 서버화의 주된 이유다.

#### 목적

- 에이전트 측 SDK는 **HTTP 한 번만 부르면 되는 경량 클라이언트**로 축소
- 인프라 의존(MDB / Redis / LLM Handler / file-manager)은 단일 Service(FastAPI, port 8101)가 일괄 책임
- 환경변수를 `LLM_REQUEST_URL` 하나로 일원화, 초기화 코드 전면 제거
- 차후 에이전트별 호출 quota / rate limit을 Service 레이어에서 제어할 수 있는 구조 확보

#### 코드 세부 설명

패키지 디렉터리를 `sdk/` + `service/` 2-tier로 재구성했다.

- **SDK (`sdk/llm_request/`)**: Service 엔드포인트를 호출하는 httpx 비동기 클라이언트가 중심(`_client.py`). 대부분의 공개 함수는 Service 호출을 감싼 얇은 래퍼지만, **외부 사용자 요구로 다음 2개는 SDK에서 직접 노출**한다.
  - `done_check(llm_input, files=...)` — LLM 호출 없이 **캐시 존재 여부만** 확인하고 싶을 때
  - `fetch_by_cache_key(cache_key)` — 이미 확보한 `cache_key` 하나로 **결과만 직접 조회**하고 싶을 때

- **Service (`service/app/`)**: FastAPI 앱. 기존 라이브러리의 로직 파일들이 `service/app/logic/`로 전부 이동됐다.
  - `main.py`의 `lifespan`에서 handler 3종(MDB / Redis / LLM) 연결과 Redis 상태 검증을 수행
  - `_flush_stale_standbys()` — 이전에 Service가 캐시를 생성하는 도중 중단되면 해당 요청의 **standby 키가 Redis에 남아**, 후속 요청이 잘못된 dummy/타임아웃 상태로 대기하는 문제가 생길 수 있다. 이를 방지하기 위해 startup 시점에 stale 키를 일괄 정리한다. Cache stampede dedup 메커니즘과 관련되며, 상세는 Task 11 참고

##### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 경로 이동 | `llm_request/runner.py` → `service/app/logic/runner.py` | 로직 유지, 파일 위치만 변경 |
| 경로 이동 | `llm_request/keying.py` → `service/app/logic/keying.py` | 내용 정밀화는 Task 4 |
| 경로 이동 | `llm_request/done_check.py` → `service/app/logic/done_check.py` | |
| 경로 이동 | `llm_request/store.py` → `service/app/logic/store.py` | 필드명 변경은 Task 5 |
| 경로 이동 | `llm_request/meta.py` → `service/app/logic/meta.py` | |
| 경로 이동 | `llm_request/orchestrator.py` → `service/app/logic/orchestrator.py` | |
| 경로 이동 + 축소 | `llm_request/__init__.py` → `sdk/llm_request/__init__.py` | HTTP 호출 래퍼 중심으로 축소 |
| 신규 | `sdk/llm_request/_client.py` | httpx `AsyncClient` 싱글톤 |
| 신규 | `sdk/llm_request/aio.py` | `llm_handler.aio` 재수출 (상세 Task 13) |
| 신규 | `sdk/llm_request/models.py` | `FileInput`·`RequestMeta`·`Timings` 등 (상세 Task 2·3) |
| 신규 | `sdk/llm_request/exceptions.py` | SDK 예외 (상세 Task 12) |
| 신규 | `service/app/main.py` | FastAPI 앱 + lifespan 초기화 |
| 신규 | `service/app/config.py` | Pydantic Settings (환경변수) |
| 신규 | `service/app/core/providers.py` | handler 의존성 주입 + Redis 상태 검증 |
| 신규 | `service/app/core/errors.py` | Service 에러 계층 (상세 Task 12) |
| 신규 | `service/app/routers/request.py` | 4개 엔드포인트 정의 (상세 Task 7) |
| 신규 | `service/app/models/requests.py` · `responses.py` | HTTP 요청/응답 DTO |

> `service/app/logic/file_handler/` (file-manager와 다른 **내부 파일 처리 모듈**)는 Task 8 ~ 11에서 별도로 다룬다.

#### 변경된 코드 — 사용자 관점 Before / After

Before (v0.1.0)
```python
import os
os.environ["MDB_HANDLER_URL"] = "http://192.7.9.45:8300"
os.environ["LLM_HANDLER_URL"] = "http://192.7.9.45:8100"
os.environ["MONGODB_DATABASE"] = "kw_ai"

from mdb_handler import MongoDBHandler
MongoDBHandler.reset()

import llm_handler
llm_handler.configure()
import llm_handler.aio
llm_handler.aio.configure()

from llm_request import llm_request_async
```

After (v0.2.0)
```python
# LLM_REQUEST_URL은 기본값(http://192.7.9.45:8101) 내장 — URL이 다를 때만 설정
# os.environ["LLM_REQUEST_URL"] = "http://192.7.9.45:8101"

from llm_request import llm_request_async
```

#### 영향 범위

- **Breaking**: SDK 설치 경로 `packages/llm-request` → `packages/llm-request/sdk`
- **Breaking**: 초기화 보일러플레이트 전면 제거 필요 (`MongoDBHandler.reset()`, `llm_handler.configure()` 등)
- **운영**: Service(port 8101)를 별도 배포·실행 필수 (`uvicorn app.main:app --port 8101`)
- **에이전트 의존성 감소**: MongoDB / LLM Handler 직접 의존 제거, 전부 Service 경유
- **Redis 배포 필수** — Task 6 참조

#### 연관 Task

- Task 2 (SDK 파라미터 정리): SDK 시그니처가 이 분리를 전제로 재설계됨
- Task 7 (Service 엔드포인트 4개): 이 분리의 구체적 API 계약
- Task 11 (Cache stampede dedup): lifespan의 standby 키 정리와 연결
- Task 12 (Error type hierarchy): HTTP 경계가 생기면서 에러 분류 체계화 필요

#### 차후 계획

- 에이전트별 호출 quota / rate limit을 Service 레이어 Semaphore로 도입 검토

---

### Task 2. SDK 파라미터 정리 (`Timings` 도입, `ttl_seconds`·`as_stream` 제거, 응답 타입 개선)

#### 배경

기존 v0.1.0 SDK는 시간 관련 파라미터가 평면적으로 나열돼 있었고, 이후 Context Cache TTL / HTTP timeout / Cache stampede dedup TTL 같은 시간 관련 파라미터가 추가될 예정이었기 때문에 전부 개별 인자로 쌓으면 시그니처가 금방 비대해질 수밖에 없었다. 또 다음 두 지점도 정리 대상이었다.

- `fetch_by_cache_key(..., as_stream=False)` — 캐시에는 이미 완성형 응답이 저장돼 있어 stream 여부를 구분할 실익이 없었음
- 응답 `meta`가 dict라서 IDE 자동완성·타입 힌트 혜택을 못 받음

#### 목적

- 시간 관련 파라미터를 **`Timings` TypedDict** 하나로 묶어 향후 추가에도 대응 가능한 구조 확보
- `fetch_by_cache_key`의 `as_stream` 제거 — 저장된 값은 이미 완성형이라 stream 구분이 무의미
- 응답 `meta` 를 **`RequestMeta` BaseModel**로 교체해 속성 접근·타입 힌트 제공

#### 코드 세부 설명

##### `Timings` TypedDict

`total=False`라 일부 키만 넘기면 나머지는 기본값으로 채워진다.

| 키 | 기본값 | 역할 |
|-----|-------|------|
| `http_timeout` | `600.0` | SDK → Service HTTP 응답 대기 시간(초) |
| `mdb_ttl` | `None` | MDB 캐시 수명(초). `None` = 영구 저장 |
| `context_cache_ttl` | `None` | Gemini Context Cache 수명(초). **`None`이면 Context Cache 기능 자체가 비활성화되고, 값이 있으면 해당 초만큼 Context Cache를 생성·유지** (Task 10) |
| `standby_ttl` | `600` | Cache stampede dedup standby 키 수명·대기 timeout(초) (Task 11) |

정의 위치: `sdk/llm_request/__init__.py:145-176`. 내부 `_resolve_timings()`가 사용자 입력과 기본값을 병합한다.

##### 시그니처 변화 요약

Before (v0.1.0)
```python
llm_request_async(
    llm_input, *,
    cache_mode=True,
    save_mode=True,
    ttl_seconds=None,
    debug=False,
    database=None,
)
fetch_by_cache_key(cache_key, *, database=None, as_stream=False)
```

After (v0.2.0)
```python
llm_request_async(
    llm_input, *,
    files=None,              # Task 3
    cache_mode=True,
    save_mode=True,
    database=None,
    timings=None,            # ← 신규 (Timings TypedDict)
    debug=False,             # 동작 변경 없음 (route_reason 제어)
)
fetch_by_cache_key(cache_key, *, database=None)
```

> `debug` 파라미터 자체는 v0.1.0 그대로 유지된다. `True`일 때만 `meta.route_reason`에 요청 처리 경로가 기록되는 동작도 동일.

##### 응답 `meta` 타입 변경 (dict → RequestMeta BaseModel)

응답 `meta`가 Pydantic BaseModel(`RequestMeta`)로 바뀌어 속성 접근을 쓴다. 필드 구성 자체는 동일.

```python
# Before (v0.1.0) — dict
print(meta["source"])
print(meta.get("route_reason"))

# After (v0.2.0) — 속성
print(meta.source)
print(meta.route_reason)
```

MDB 저장측 문서 스키마 변경은 Task 5에서 다룬다(별개 이슈).

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `sdk/llm_request/__init__.py` | `ttl_seconds` 제거, `files`/`timings` 추가, `Timings` TypedDict 정의 및 `_resolve_timings()` |
| 변경 | `sdk/llm_request/__init__.py` (`fetch_by_cache_key`) | `as_stream` 파라미터 제거 |
| 변경 | `sdk/llm_request/models.py` | `RequestMeta` BaseModel 도입 (응답 dict → 속성 접근) |
| 변경 | `service/app/routers/request.py` | `fetch_by_cache_key` 엔드포인트에서 스트림 반환 경로 제거 |

#### 사용자 관점 Before / After

Before (v0.1.0)
```python
response, meta = await llm_request_async(
    {"contents": "질문", "model": "gemini-2.5-flash"},
    ttl_seconds=3600,
)
print(meta["source"])
```

After (v0.2.0)
```python
response, meta = await llm_request_async(
    {"contents": "질문", "model": "gemini-2.5-flash"},
    timings={"mdb_ttl": 3600, "context_cache_ttl": 1800},
)
print(meta.source)
```

#### 영향 범위

- **Breaking**: `ttl_seconds` 파라미터 제거 → `timings={"mdb_ttl": ...}` 로 이관
- **Breaking**: `as_stream` 파라미터 제거 (`fetch_by_cache_key`)
- **Breaking**: `meta` 응답이 dict → `RequestMeta` BaseModel (속성 접근)
- **비-breaking**: `debug` / `cache_mode` / `save_mode` / `database` 는 기존 동작 유지

#### 연관 Task

- Task 1 (SDK + Service 분리): 본 정리는 분리된 SDK의 시그니처 재설계 일환
- Task 3 (파일 입력 패러다임 변경): `files` 인자 추가와 세트
- Task 5 (MDB 문서 스키마 개선): 저장측 문서 스키마는 별개 맥락
- Task 10 (Context Cache): `context_cache_ttl`의 활성 여부를 이 키로 제어
- Task 11 (Cache stampede dedup): `standby_ttl`은 `Timings`의 한 키

---

### Task 3. 파일 입력 패러다임 변경 (`contents` 안 `FileRef` → `files=[FileInput]`)

#### 배경

기존 v0.1.0에서 파일을 LLM에 전달하려면 에이전트가 다음 단계를 직접 수행해야 했다.

- `llm_handler.upload_file()`로 Gemini Files API에 업로드해 `FileRef` 획득
- 혹은 `Part.from_bytes()` / `Part.from_uri()`로 Part 구성
- `llm_input["contents"]`에 문자열과 섞어 전달
- 크기에 따라 inline(≤20MB) vs Files API 본인이 판단, 50MB 초과 PDF split은 에이전트가 구현
- 재사용 시 URI 만료·재업로드 관리

이 구조의 본질적 제약:
- **캐시 키가 file URI 기반** → 같은 파일을 재업로드하면 URI가 바뀌어 cache miss
- **SDK + Service 분리 후**(Task 1) Service는 에이전트 파일 시스템에 접근 불가 → URI만 넘기면 Service가 원본 바이트를 확보할 방법이 없음

#### 목적

- 에이전트는 **FileInput(메타 + bytes)** 만 구성해 `files=[...]`로 넘기면 끝
- inline vs Files API 분기, PDF split, URI 캐싱, Context Cache는 **Service가 자동 처리**
- **파일 내용 기반 checksum**(`file_name` + `file_size` + `file_mtime`)으로 같은 내용이면 URI가 달라도 cache hit

#### 코드 세부 설명

##### `FileInput` — `llm_request_async(..., files=...)` 용

SDK / Service 양쪽에 동일 스키마의 Pydantic BaseModel.

| 필드 | 타입 | 설명 |
|------|------|------|
| `file_name` | `str` | 파일명(확장자 포함) |
| `file_size` | `int` | 파일 크기(bytes) |
| `file_mtime` | `str \| int` | 수정 시각 (unix timestamp 권장) |
| `mime_type` | `str` | MIME 타입 |
| `extension` | `str` | 확장자 (점 없이, 예: `"pdf"`) |
| `data` | `bytes` | 파일 바이너리. HTTP 전송 시 base64 자동 인코딩 (`Base64Field` 어노테이션) |

##### `FileKeyingMeta` — `done_check(..., files=...)` 용 경량

캐시 존재만 확인하는 상황에선 바이트가 불필요하므로 해시용 3필드만 받는다.

| 필드 | 타입 |
|------|------|
| `file_name` | `str` |
| `file_size` | `int` |
| `file_mtime` | `str \| int` |

##### Service의 자동 처리 파이프라인

`files`가 들어오면 Service 내부에서 순서대로 수행:

1. **유효성 검증** — MIME 화이트리스트 + 2GB 초과 거부 (Task 12 에러 계층 연계)
2. **file-manager에 파일 등록** — Service 진입 즉시 file-manager에 파일을 넘겨 관리 시작 (Task 8)
3. **파일 분류** — `resolver.classify_files()`가 크기·타입별로 inline 풀 / Files API 풀로 분리
   - 비디오 → 항상 Files API
   - inline 풀 합계 ≤20MB(이미지·오디오 섞일 때) / ≤50MB(PDF·텍스트만) → inline
   - 초과분 → Files API
4. **resolve_files** —
   - inline 풀: base64로 요청에 포함
   - Files API 풀: Redis URI 캐시 확인 → miss면 업로드 (Task 9)
   - 50MB 초과 PDF: 자동 split 후 업로드
   - `context_cache_ttl` 지정 시 Context Cache 생성 (Task 10)
5. LLM 호출 → MDB 저장

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `sdk/llm_request/models.py` | `FileInput`, `FileKeyingMeta` 공개 export |
| 신규 | `service/app/models/requests.py` | `FileInput`, `FileKeyingMeta`, `LLMRequestBody.files`, `DoneCheckBody.files` |
| 신규 | `service/app/logic/file_handler/` | 전체 모듈 (resolver / uploader / splitter / cache_store / dedup / validators — Task 7·9·10·11에서 세부) |
| 변경 | `sdk/llm_request/__init__.py` | `llm_request_async` 등 4개 public 함수에 `files` 인자 추가, `done_check`에 `FileKeyingMeta` 리스트 |
| 변경 | `service/app/logic/runner.py` | `files` 받아 `prepare()` / `ingest_files()` / validators / `resolve_files()` 연결 |

#### 사용자 관점 Before / After

Before (v0.1.0)
```python
from llm_request import llm_request_async, llm_handler

file_ref = llm_handler.upload_file("report.pdf")   # 에이전트가 직접 업로드
response, meta = await llm_request_async(
    {"contents": ["이 PDF 요약해줘", file_ref], "model": "gemini-2.5-flash"},
)
```

After (v0.2.0)
```python
from pathlib import Path
import mimetypes
from llm_request import llm_request_async, FileInput

p = Path("report.pdf")
fi = FileInput(
    file_name=p.name,
    file_size=p.stat().st_size,
    file_mtime=int(p.stat().st_mtime),
    mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
    extension=p.suffix.lstrip("."),
    data=p.read_bytes(),
)
response, meta = await llm_request_async(
    {"contents": "이 PDF 요약해줘", "model": "gemini-2.5-flash"},
    files=[fi],
)
```

#### 영향 범위

- **Breaking 사용 패턴**: `contents` 안에 `FileRef` / `Part` 직접 삽입하던 기존 방식은 **비권장**. 동작 자체는 가능하지만 file_handler 파이프라인을 통과하지 않아 inline/Files API 자동 분기·URI 캐싱·PDF split 자동화 혜택을 받지 못함
- **에이전트 파일 처리 코드 단순화**: 크기 임계값·URI 만료·split 로직 모두 Service 책임
- **캐시 일관성 개선**: 파일 내용 기반 checksum이라 같은 파일이면 URI가 달라도 cache hit
- **주의**: `file_name` / `file_size` / `file_mtime` 3필드는 실제 파일에서 추출한 값과 정확히 일치해야 함 — 잘못된 값은 cache miss 반복의 원인

#### 연관 Task

- Task 1 (SDK + Service 분리): Service가 파일 바이트를 받으려면 `FileInput`이 필연
- Task 2 (SDK 파라미터 정리): `files` 인자 추가는 본 변경의 SDK 측 반영
- Task 4 (캐시 키 정밀화): file checksum을 `file_checksums`로 캐시 키에 주입
- Task 8 (file-manager에 파일 등록): 파일 등록 단계
- Task 9 (Files API URI 캐시)
- Task 10 (Context Cache opt-in)
- Task 11 (Cache stampede dedup): 파일 업로드 / Context Cache 생성 중복 방지
- Task 12 (Error hierarchy): 유효성 검증 실패 에러

---

### Task 4. 캐시 키 정밀화 (`file_checksums` 주입 + `cached_content` volatile + `FileRef`/`Part` 정규화)

#### 배경

v0.1.0 canonicalize 기본 규칙(float 4자리 반올림, volatile 키 제거, dict 키 정렬, list 순서 유지, `Decimal`/`datetime`/`bytes` 변환, SHA-256 hex 64자리)은 그대로 써도 충분히 안정적이었다. 다만 파일 입력 패러다임이 `FileRef in contents` → `files=[FileInput]` (Task 3)로 바뀌면서, **파일 정보를 캐시 키에 반영하는 방식을 새로 설계할 필요**가 생겼다.

기존 구조의 제약:
- v0.1.0은 파일을 `contents` 안 `FileRef`로 넣으면 `FileRef.uri`가 그대로 canonicalize돼 캐시 키에 반영됨 → 같은 파일을 재업로드하면 URI가 바뀌어 cache miss
- Context Cache name(`cached_content`)도 Gemini가 새로 생성할 때마다 달라짐 → 같은 파일 조합이어도 cache miss

#### 목적

- 파일 정보를 **내용 기반 checksum**으로 압축해 `file_checksums` 필드로 주입
- 파일이 있을 때 `cached_content`는 **volatile 처리** (`""`로 치환)
- `FileRef` / `Part` 객체도 파일이 있을 때는 **volatile 속성(URI, base64 bytes 등) 제거** 후 타입/MIME만 남도록 정규화
- v0.1.0에서 이미 쓰던 canonicalize 규칙은 **그대로 유지**, 확장만 수행

#### 코드 세부 설명

##### subset 필드 15 → 16

`keying.py:23-40`의 `_LLM_INPUT_FIELDS`에 `file_checksums` 신설.

| 범주 | 필드 | v0.1.0 대비 |
|------|------|-------------|
| 유지 | `contents`, `system`, `history`, `model`, `max_tokens`, `temperature`, `service_name`, `seed`, `output`, `schema`, `thinking`, `tools`, `tool_config`, `media_resolution` | 규칙 동일 |
| 의미 변경 | `cached_content` | **파일 있으면 `""`로 치환(volatile)** — `build_llm_input_subset:78-80` |
| 신규 | `file_checksums` | 파일 checksum 리스트 |

##### `make_file_checksum(meta)` — 파일 내용 checksum

`FileInput` / `FileKeyingMeta`의 3필드(`file_name`, `file_size`, `file_mtime`)를 `canonicalize_llm_input`에 통과시킨 뒤 SHA-256. 기존 canonicalize 로직을 그대로 재사용한다. (`keying.py:242-267`)

```python
file_meta = {
    "file_name":  meta.file_name,
    "file_mtime": meta.file_mtime,
    "file_size":  meta.file_size,
}
canonical = canonicalize_llm_input(file_meta)
return make_cache_key(canonical)  # 64자리 SHA-256 hex
```

이 checksum은 3곳에서 재사용된다.

| 용도 | 위치 |
|------|------|
| LLM 응답 캐시 키의 `file_checksums` 리스트 | 본 Task |
| Redis `file_cache:files_api:{checksum}` 키 | Task 9 |
| Redis `file_cache:context_cache:{...}` 구성 요소 | Task 10 |

##### `FileRef` / `Part` 정규화 (파일 있을 때만)

`canonicalize_llm_input`의 `_normalize` 내부. `file_checksums`가 있으면 URI·base64 hex 같은 **volatile 속성은 제거**하고 타입·MIME만 남긴다. (`keying.py:155-160`)

```python
if has_file_checksums:
    if isinstance(obj, FileRef):
        return {"_type": "file_ref", "mime_type": obj.mime_type}
    if isinstance(obj, Part):
        return {"_type": f"{obj.type}_part", "mime_type": obj.mime_type}
```

결과적으로 `contents` 안에 파일 객체가 섞여 있어도 URI 변화가 캐시 키를 흔들지 않는다.

##### 유지되는 v0.1.0 canonicalize 규칙

- float 4자리 반올림
- `Decimal.normalize()` → str
- `datetime`/`date` isoformat, `bytes` hex
- dict 키 알파벳 정렬, list 순서 유지
- volatile 키 제거 (`timestamp`, `ts`, `created_at`, `updated_at`, `request_id`, `trace_id`)
- SHA-256 hex 64자리

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `service/app/logic/keying.py` (`_LLM_INPUT_FIELDS`) | `file_checksums` 추가 (15 → 16 필드) |
| 변경 | `service/app/logic/keying.py` (`build_llm_input_subset`) | 파일 있을 때 `cached_content`를 `""`로 치환 |
| 변경 | `service/app/logic/keying.py` (`canonicalize_llm_input`) | `FileRef`/`Part` 정규화 로직 추가 (파일 있을 때) |
| 신규 | `service/app/logic/keying.py` (`make_file_checksum`) | 파일 메타 3필드로 SHA-256 |

#### 사용자 관점 Before / After

Before (v0.1.0) — 같은 파일 재업로드 시 cache miss
```python
ref1 = llm_handler.upload_file("report.pdf")   # URI: gs://.../abc
resp1, meta1 = await llm_request_async({"contents": ["요약", ref1], ...})

ref2 = llm_handler.upload_file("report.pdf")   # URI: gs://.../xyz  ← 새 업로드
resp2, meta2 = await llm_request_async({"contents": ["요약", ref2], ...})
# meta2["cache_hit"] == False — URI가 달라서 캐시 키도 다름
```

After (v0.2.0) — URI 무관, 파일 내용 동일하면 cache hit
```python
fi = FileInput(file_name="report.pdf", file_size=..., file_mtime=..., ...)
resp1, meta1 = await llm_request_async({"contents": "요약", ...}, files=[fi])
resp2, meta2 = await llm_request_async({"contents": "요약", ...}, files=[fi])
# meta2.cache_hit == True — file_checksums 동일
```

#### 영향 범위

- **Breaking 캐시 호환성**: v0.1.0 캐시와 키 호환 안 됨 (`file_checksums` 추가·`cached_content` volatile 처리로 해시 자체가 달라짐) → 배포 초기 cache miss·LLM 재호출 비율 일시 증가
- **FileRef / Part 혼용 호환**: 기존 스타일대로 `contents`에 `FileRef`를 섞어 넣어도 `file_checksums`가 있으면 정규화되어 캐시 키가 안정됨

#### 연관 Task

- Task 3 (파일 입력 패러다임 변경): `file_checksums`는 이 단계에서 주입됨
- Task 5 (MDB 문서 스키마): `canonical_json` 저장 방식
- Task 9 (Files API URI 캐시): 동일 checksum으로 Redis URI 캐시 키 구성
- Task 10 (Context Cache opt-in): 동일 checksum이 context_cache Redis 키 구성 요소

---

### Task 5. MDB 문서 스키마 개선 (`meta` → `metadata`, `file_meta` 추가, `elapsed_ms` 제거)

#### 배경

- v0.1.0에서 **저장 문서의 `meta` 필드**와 **응답의 `meta` dict**가 이름이 같아 혼동을 유발. mdb-handler 측과 논의해 **저장측 키 이름을 `metadata`로 통일**하기로 합의
- 파일 입력 패러다임 전환(Task 3) 이후 파일 메타도 문서에 남겨야 감사·디버깅이 가능한데 별도 필드가 없었음
- `elapsed_ms`는 LLM 소요시간 측정용이었지만 rate limit / queue 대기 때문에 "순수 LLM 처리 시간"을 분리 측정할 수 없어 의미가 흐려짐. 또 llm-handler 응답 자체에 시간 관련 정보가 포함되어 있어 중복

#### 목적

- 저장 문서 필드명 `meta` → **`metadata`** 로 통일 (mdb-handler와 합의한 이름)
- 파일 포함 요청에 한해 **`file_meta` 필드 신설** — `FileInput`의 메타(`data` 제외)를 리스트로 저장
- `elapsed_ms` **제거**

#### 코드 세부 설명

##### v0.1.0 → v0.2.0 문서 스키마 diff

| 필드 | v0.1.0 | v0.2.0 | 비고 |
|------|--------|--------|------|
| `_id` | ✅ | ✅ | SHA-256 캐시 키 |
| `canonical_json` | ✅ | ✅ | 변경 없음 |
| `result` | ✅ | ✅ | `LLMResponse.model_dump()` |
| `meta` | ✅ | ❌ | 필드명 변경 → `metadata` |
| `metadata` | ❌ | ✅ | **신규(이름 교체)** — `{service_name, model}` |
| `created_at` | ✅ | ✅ | UTC ISO |
| `requested_at` | ✅ | ✅ | LLM 호출 시에만 |
| `elapsed_ms` | ✅ | ❌ | **제거** |
| `expires_at` | ✅ | ✅ | `mdb_ttl` 지정 시만 |
| `file_meta` | ❌ | ✅ | **신규** — 파일 포함 요청에 한해 `FileInput` 메타(`data` 제외) 리스트 |

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `service/app/logic/store.py` (`save_result`) | `meta` → `metadata` 키, `file_metas` / `requested_at` 파라미터 추가, `elapsed_ms` 제거 |
| 변경 | `service/app/logic/store.py` (`fetch_by_cache_key`) | 응답 구성 시 `doc.get("metadata", {})` 로 조회 |
| 변경 | `service/app/logic/runner.py` | `finalize` / `_finalize_stream` 에서 `file_metas` · `requested_at` 전달, `_dump_files_meta` 헬퍼 신설 |

#### 문서 예시 Before / After

Before (v0.1.0)
```json
{
  "_id": "abc...",
  "canonical_json": "...",
  "result": {"text": "...", "model": "..."},
  "meta": {"service_name": "kiwoom-rm", "model": "gemini-2.5-flash"},
  "created_at": "2026-01-15T03:22:11+00:00",
  "requested_at": "2026-01-15T03:22:08+00:00",
  "elapsed_ms": 2841,
  "expires_at": "2026-01-15T04:22:11+00:00"
}
```

After (v0.2.0)
```json
{
  "_id": "abc...",
  "canonical_json": "...",
  "result": {"text": "...", "model": "..."},
  "metadata": {"service_name": "kiwoom-rm", "model": "gemini-2.5-flash"},
  "created_at": "2026-01-15T03:22:11+00:00",
  "requested_at": "2026-01-15T03:22:08+00:00",
  "expires_at": "2026-01-15T04:22:11+00:00",
  "file_meta": [
    {"file_name": "report.pdf", "file_size": 1048576, "file_mtime": 1705280000,
     "mime_type": "application/pdf", "extension": "pdf"}
  ]
}
```

#### 영향 범위

- **Breaking 직접 조회**: 외부에서 MDB 문서를 직접 조회하던 코드는 `doc["meta"]` → `doc["metadata"]` 로 수정 필요
- **`elapsed_ms` 의존 코드**: 해당 필드를 참조하던 대시보드·분석 스크립트가 있다면 제거 필요

#### 연관 Task

- Task 2 (SDK 파라미터 정리): 응답 `meta` BaseModel과 저장 `metadata` 필드의 이름·역할 명확화
- Task 3 (파일 입력 패러다임 변경): `file_meta` 저장으로 파일 감사 근거 확보
- Task 4 (캐시 키 정밀화): `canonical_json` 저장 방식은 동일 유지

---

### Task 6. Redis 필수화 (미연결 시 Service 기동 실패)

#### 배경

v0.1.0에서는 파일 업로드·Context Cache 생성을 **에이전트가 미리 수행**하고, 그 결과물(`FileRef.uri` / `cache_name`)을 `llm_handler.call()`에 직접 인자로 넘기는 방식이었다. 즉 "이전에 업로드·캐싱한 게 있음"은 **에이전트가 기억**했다.

v0.2.0에서는 파일 업로드·캐싱 책임이 **Service로 이전**되면서(Task 3 / 9 / 10) 에이전트는 `FileInput`(bytes)만 넘긴다. 그런데 Service의 호출들은 서로 독립적이라, Service 스스로 **"이 파일을 이전에 업로드했나?" / "이 파일 조합의 Context Cache가 이미 있나?"** 를 기억할 수단이 필요해졌다.

그 기억은 **외부 저장소**에 둬야 프로세스 간·인스턴스 간 공유되고 재시작에도 살아남는다 — Redis가 그 역할. 또 Cache stampede dedup(Task 11)의 standby 키도 Redis 기반. 따라서 v0.2.0에서는 Redis를 단순 옵션이 아니라 **인프라 전제**로 승격했다.

이 구조상 Redis 미연결은 곧 기능 미동작이므로, 메모리 fallback으로 뜬 채 운영에서 뒤늦게 이상을 발견하기보다 **startup 시점에 fail-fast**로 감지하는 편이 안전하다.

#### 목적

- Service startup 시점에 Redis 연결을 **fail-fast**로 검증
- 이전에 crash로 남은 stale standby 키 정리도 이 단계에서 수행 (Task 11과 연결)

#### 코드 세부 설명

`service/app/core/providers.py` 에 2단계 초기화 추가 — `configure_handlers()` 직후 차례로 호출.

```python
def _ensure_redis_connected() -> None:
    """Redis 연결 확인. 실패 시 RuntimeError — Service 기동 중단."""
    if not redis_handler.is_connected():
        raise RuntimeError(
            "Redis 미연결 — llm-request Service는 Redis가 필수입니다. "
            "REDIS_URL 환경변수와 Redis 프로세스를 확인하세요."
        )


def _flush_stale_standbys() -> None:
    """이전 crash 이후 남은 standby 키 정리 (상세 Task 11)."""
    ...
```

`service/app/main.py`의 `lifespan`이 startup 시 순서대로 호출. Redis 미연결이면 `RuntimeError`가 발생해 **uvicorn이 기동을 중단**하므로, 서비스가 잘못된 상태로 뜨지 않는다.

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/core/providers.py` (`_ensure_redis_connected`) | Redis 연결 fail-fast 검증 |
| 신규 | `service/app/core/providers.py` (`_flush_stale_standbys`) | stale standby 키 정리 (상세 Task 11) |
| 변경 | `service/app/main.py` (`lifespan`) | startup 시 위 2개 함수 호출 연결 |

#### 영향 범위

- **배포**: Redis 인프라 반드시 확보 필요. 미연결이면 Service 기동 자체가 `RuntimeError`로 실패해 `uvicorn`이 뜨지 않음 (메모리 fallback 없음)
- **운영 진단 용이**: 잘못된 Redis 설정이 런타임 이상 증상이 아닌 **startup 로그로 즉시 드러남**

#### 연관 Task

- Task 1 (SDK + Service 분리): lifespan 초기화 흐름의 일부
- Task 9 (Files API URI 캐시): Redis URI 캐시 전제
- Task 10 (Context Cache opt-in): Context Cache name 저장 전제
- Task 11 (Cache stampede dedup): 3계층 standby SETNX 전제

#### 차후 계획

- 현재는 Redis만 startup fail-fast 검증 중. **MDB Handler / LLM Handler도 동일하게 앞단에서 막을지**는 추후 고민 후 반영 예정

---

## B. 새로 추가된 로직

### Task 7. Service 엔드포인트 4개 (FastAPI, port 8101)

#### 배경

SDK 공개 함수 4개를 엔드포인트 4개로 매핑한다.

- `llm_request` / `llm_request_stream` — 요청 입력은 동일하지만 **응답 형식이 JSON vs SSE**로 달라 엔드포인트 분리 필연
- `done_check` / `fetch_by_cache_key` — 각자 용도·요청 body 구조가 달라 **별도 엔드포인트**가 자연스러움

#### 코드 세부 설명

##### 엔드포인트 ↔ SDK 매핑

| 엔드포인트 | SDK 대응 | 역할 | 요청 body | 응답 |
|-----------|---------|------|-----------|------|
| `POST /v1/request` | `llm_request_async` / `llm_request` | 단건 LLM 호출 (캐시 조회 + 저장 포함) | `LLMRequestBody` | `LLMRequestResponse` |
| `POST /v1/request/stream` | `llm_request_stream_async` / `llm_request_stream` | 스트리밍 LLM 호출 | `LLMRequestBody` (동일) | **SSE** — `data: {chunk json}` |
| `POST /v1/done-check` | `done_check` | 캐시 존재 여부만 확인 (LLM 호출 X) | `DoneCheckBody` | `DoneCheckResponse` |
| `POST /v1/fetch` | `fetch_by_cache_key` | 캐시 키로 직접 조회 | `FetchBody` | `FetchResponse` |

각 DTO의 필드 상세는 [README_v2.md 섹션 9](../README_v2.md)를 참고.

##### SSE 응답 형식 (`/v1/request/stream`)

```
data: {"type": "chunk", "text": "첫 번째 ", "finish_reason": null}
data: {"type": "chunk", "text": "조각", "finish_reason": "STOP", "usage": {...}}
data: [DONE]
```

SDK 클라이언트가 `data: ` 접두를 파싱하여 JSON 또는 `[DONE]` 센티넬 처리. `[DONE]` 수신 시 이터레이터 종료.

##### 요청 body 특징

- 모든 엔드포인트가 `LLMRequestBody` / `DoneCheckBody` / `FetchBody` 같은 **Pydantic BaseModel**로 타입 검증 (422 → `validation` 에러)
- `files` 필드는 `LLMRequestBody`에서만 (Task 3)
- `FileKeyingMeta`는 `DoneCheckBody`에서만 (해시용 경량)

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/routers/request.py` | 4개 엔드포인트 정의 |
| 신규 | `service/app/models/requests.py` | `LLMRequestBody`, `DoneCheckBody`, `FetchBody`, `FileInput`, `FileKeyingMeta` |
| 신규 | `service/app/models/responses.py` | `LLMRequestResponse`, `DoneCheckResponse`, `FetchResponse`, `ErrorResponse` |

#### 영향 범위

- **엔드포인트 확장 시 새 경로 추가 원칙**: 기존 엔드포인트에 기능을 얹기보다 신규 `/v1/…`로 추가하여 하위 호환 유지

#### 연관 Task

- Task 1 (SDK + Service 분리): 본 엔드포인트 구조의 상위 맥락
- Task 3 (파일 입력 패러다임): `files` 필드의 DTO 등록
- Task 12 (Error hierarchy): 4xx / 5xx 응답 형식 (`ErrorResponse.error_type`)

---

### Task 8. file-manager 신설 — Service 진입 시 사후 로깅용 파일 등록

#### 배경

**AI 전용 파일 서버(file-manager)** 에 LLM 요청에 사용된 파일들을 적재하기 위해, Service가 진입 시점에 파일의 메타정보와 bytes를 전달해 등록한다. 이미 등록된 파일이면 저장 함수를 호출하지 않는다(`check_cache` 먼저 확인).

이 책임은 llm-request Service가 전담하며 에이전트는 file-manager의 존재를 알 필요 없다.

#### 코드 세부 설명

`service/app/logic/ingest.py` 신설. runner의 public 함수(`llm_request_async` / `llm_request_stream_async`)가 `FileInput` 유효성 검증 직전에 호출한다.

```python
async def ingest_files(files: list[FileInput]) -> None:
    """Service 진입 시 file-manager에 파일 존재 확인 + 필요 시 업로드.

    side-effect 전용 — 반환값 없음. file-manager가 부여하는 file_path 등
    내부 식별자는 일체 사용하지 않는다.
    """
    for fi in files:
        hit = await file_manager.check_cache(
            file_name=fi.file_name,
            file_size=fi.file_size,
            file_mtime=fi.file_mtime,
        )
        if not hit:
            await file_manager.upload_file(
                data=fi.data,
                file_name=fi.file_name, ...
            )
```

file-manager가 일시 장애를 겪어도 예외를 격리하고 LLM 호출은 계속 진행한다.

> **주의사항**
> - **llm-request 테스트·개발 목적으로 동일 로직의 간이 file-manager를 구축해 실험을 진행**했다. 정식 file-manager 담당자가 완성한 버전으로 **나중에 교체 예정**
> - 본 문서는 정식 file-manager 기준 이름(`check_cache` / `upload_file` / `get_file`)으로 표기. 현재 간이 구현 SDK에는 `a` prefix(`acheck_cache` / `aupload_file` / `aget_file`)가 붙어 있으나, 정식 file-manager는 **async-only 제공** 예정이라 prefix를 떼는 방향으로 담당자 확정됨

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/logic/ingest.py` | `ingest_files(files)` — side-effect 등록 로직 |
| 변경 | `service/app/logic/runner.py` | `llm_request_async` / `llm_request_stream_async` 진입 시 `ingest_files` 호출 |
| 신규 (별도 패키지) | `packages/file-manager/` | 사후 로깅·감사용 파일 저장 서비스. **간이 구현 상태 — 참고사항 C** |

#### 영향 범위

- **에이전트는 file-manager를 몰라도 됨** — `FileInput` 5필드만 구성하면 llm-request가 내부적으로 등록
- **file-manager 장애 격리**: 로깅 저장소가 내려가도 LLM 호출 경로는 계속 동작
- **무결성 검증은 비-목표**: 에이전트가 준 bytes와 file-manager 저장본의 일치 여부는 검증하지 않음 (로깅 조회 빈도가 낮음)

#### 연관 Task

- Task 3 (파일 입력 패러다임 변경): `FileInput`의 bytes가 Service로 들어오는 구조가 전제
- **참고사항 C**: 현재 file-manager는 **간이 구현 상태**. 정식 구현은 담당자 완성 시 교체 예정

---

### Task 9. Files API URI Redis 캐시 + 404 자동 복구

#### 배경

v0.1.0에는 Service가 Gemini Files API에 업로드하는 경로 자체가 없었다. Task 3에서 파일 입력 패러다임이 바뀌어 **Service가 업로드 주체**가 되면서, 같은 파일을 매 요청마다 재업로드하지 않도록 캐시가 필요해졌다. Gemini Files API는 업로드 후 **48시간** 살아있다가 자동 만료되는데, 그 안에는 기존 URI를 재사용할 수 있다.

#### 목적

- 업로드한 URI를 **Redis에 캐시**해 같은 파일의 중복 업로드를 회피
- Gemini 측 URI가 만료(404)된 경우 **1회 자동 복구** — 호출자에 투명하게 재업로드 + 재시도

#### 코드 세부 설명

##### Redis URI 캐시

- **키**: `file_cache:files_api:{checksum}` (checksum = Task 4의 `make_file_checksum`)
- **값**: Gemini가 부여한 `file_uri`
- **TTL**: Gemini 응답의 만료 시각 기준 동적 계산, **1시간 버퍼 차감**. 계산 실패 시 **47시간 fallback**
- Gemini 48h > Redis 47h로 1시간 짧게 유지해 "Redis HIT인데 Gemini는 만료" 상황을 최소화

##### 업로드 경로

`file_handler/resolver.py` 의 Files API 분기:

1. `file_cache:files_api:{checksum}` 조회
2. **HIT**: 해당 URI로 Part 구성 → 업로드 생략
3. **MISS**: `uploader.upload_file()` 호출 → 업로드 후 Redis 저장

##### 404 자동 복구

Gemini가 `404 / not found`로 응답하면 (message 기반 판정):

1. `_is_gemini_404(exc)` 로 감지
2. `resolve_files_after_404(...)` 호출 — 해당 checksum의 `file_cache:files_api:{checksum}` 및 `file_cache:context_cache:{hash_key}`(Task 10) 키 삭제
3. `resolver.resolve_files` 재호출 (Redis MISS 경로로 자연 유입 → 재업로드)
4. `aio_call` / `aio_stream` 재시도 **1회만** — 두 번째 404는 실제 오류이므로 그대로 raise

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/logic/file_handler/cache_store.py` | Redis `files_api` / `context_cache` get·save·delete |
| 신규 | `service/app/logic/file_handler/uploader.py` | `upload_file()` Gemini 업로드 + Redis 저장 |
| 신규 | `service/app/logic/file_handler/resolver.py` | `resolve_files` / `resolve_files_after_404` — 분류 + URI 캐시 + 404 복구 orchestration |
| 신규 | `service/app/logic/file_handler/splitter.py` | PDF >50MB 자동 분할 |
| 변경 | `service/app/logic/runner.py` | `aio_call` / `aio_stream` try/except → `_is_gemini_404` 감지 시 `resolve_files_after_404` + 1회 재시도 |

#### 영향 범위

- **Gemini API 부하 감소**: 동일 파일을 47h 내 재사용 시 재업로드 회피 (quota·업로드 시간 절감)
- **TTL 경계 투명 복구**: 47~48h 사이 Redis 만료 상황에서도 한 번의 재업로드로 복구 — 호출자는 별도 처리 불필요

#### 연관 Task

- Task 3 (파일 입력 패러다임 변경): Service 업로드 주체 전환의 상위 맥락
- Task 4 (캐시 키 정밀화): 동일 `make_file_checksum`이 Redis 키로 재사용됨
- Task 6 (Redis 필수화): 본 캐시의 전제
- Task 10 (Context Cache opt-in): 동일 404 복구 흐름이 Context Cache 키도 함께 무효화
- Task 11 (Cache stampede dedup): 동시 업로드 중복 방지와 연결

#### 차후 계획

- **`list_files` + `sha256_hash` 기반 복구**: Redis 만료(47h) ~ Gemini 만료(48h) 사이 1h 오버랩에서 재업로드 없이 URI 복구 가능. llm-handler에 `list_files` / `FileRef.sha256_hash` 노출이 필요해 현재 **백로그** ([docs/11.files-api-uri-recovery.md](11.files-api-uri-recovery.md))
- **Gemini Files API 20GB 한도 자동 관리**: 계정당 누적 저장 용량 한도에 근접하면 오래된 파일부터 `delete_file`로 회수하는 로직. 차후 진행 예정

---

### Task 10. Context Cache opt-in — Service 자동 생성·재사용

#### 배경

v0.1.0은 에이전트가 **직접 Context Cache를 생성**하고 반환된 cache name을 `cached_content` 인자로 `llm_handler.call()`에 넘기는 방식이었다. 언제 만들고, 언제 삭제하고, 같은 파일 조합이면 재사용할지 모두 에이전트 책임이었다.

Task 3에서 파일 입력 패러다임이 전환되면서 파일 관리 주체가 Service로 넘어왔으므로, **Context Cache도 Service가 자동 관리**하는 게 자연스러워졌다.

#### 목적

- 사용자가 `timings.context_cache_ttl`만 지정하면 **Service가 Context Cache를 자동 생성**, 이후 같은 파일 조합 요청에 **자동 재사용**
- 에이전트가 `cached_content`를 직접 설정할 필요 없음 — Service가 주입
- **opt-in** 정책 — 지정 안 하면 생성 안 함 (비용 방지)

#### 코드 세부 설명

##### Redis 키·TTL

- **키**: `file_cache:context_cache:{hash_key}` (hash_key = SHA-256(`file_checksums` + `model` + `system_instruction`))
- **값**: Gemini가 반환한 Context Cache name
- **TTL**: 사용자가 준 `context_cache_ttl` (초). `None`이면 **기능 자체 비활성**

##### 동작 분기 (`file_handler/resolver.py`)

1. `context_cache_ttl`이 `None`이면 이 경로 스킵
2. Redis `file_cache:context_cache:{hash_key}` 조회
3. **HIT**: cache name을 `llm_input.cached_content`에 자동 주입 → `llm_handler.aio.call`에 전달
4. **MISS**: Gemini `create_context_cache()` 호출 → 받은 cache name을 Redis 저장 + `cached_content`에 주입

##### inline → Files API 자동 승급

Context Cache는 **Files API URI 기반** 파일만 참조 가능. 모든 파일이 inline(≤20MB)인 조합에서 `context_cache_ttl`이 지정되면, 해당 파일들을 강제로 Files API 경로로 승급 업로드한 뒤 Context Cache 생성.

##### 비용 주의

- Context Cache는 **시간당 과금** (저장 시간에 비례해 비용 발생)
- 같은 파일 조합으로 **시간당 4회 이상 재사용해야** 저장 비용 대비 90% 입력 토큰 할인이 이득 — 반복 호출이 드문 파일에는 opt-in하지 말 것
- `model` / `system_instruction`이 바뀌면 `hash_key`가 달라져 cache miss — 새 캐시가 만들어지므로 같은 파일이라도 모델 교체 시 비용 재발생

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `service/app/logic/file_handler/cache_store.py` | `context_cache` get / save / delete 추가 |
| 변경 | `service/app/logic/file_handler/uploader.py` | `create_context_cache()` Gemini 호출 래퍼 |
| 변경 | `service/app/logic/file_handler/resolver.py` | `resolve_files`에 context_cache 분기 + `cached_content` 자동 주입 + inline→Files API 승급 |

#### 영향 범위

- **opt-in**: `timings.context_cache_ttl` 미지정 시 기능 비활성
- **비용 책임은 호출자**: TTL 지정 = "이 시간 동안 저장 비용을 내겠다"는 명시. Service는 그 시간만큼 캐시 유지·재사용만 담당
- **에이전트 코드 단순화**: `cached_content` 직접 관리 불필요

#### 연관 Task

- Task 2 (SDK 파라미터 정리): `timings.context_cache_ttl`이 활성 스위치
- Task 3 (파일 입력 패러다임 변경): Service의 자동 관리가 가능해진 전제
- Task 4 (캐시 키 정밀화): `file_checksums`가 `hash_key` 구성 요소로 재사용
- Task 6 (Redis 필수화): 본 캐시의 전제
- Task 9 (Files API URI 캐시): 404 복구 시 context_cache Redis 키도 함께 무효화
- Task 11 (Cache stampede dedup): 동시 Context Cache 생성 중복 방지

#### 차후 계획

- **inline 파일 케이스의 Context Cache 경제성 재검토**: 현재는 전원 inline + `context_cache_ttl` 지정 시 Files API로 승급 후 cache 생성. 이 처리가 경제적으로 불필요하다고 판단되면, **`context_cache_ttl`을 지정하더라도 inline 케이스에서는 Context Cache를 생성하지 않고 매 호출마다 inline 그대로 전송**하는 방향으로 변경 예정. 검증 과정에서 Gemini API가 `create_cached_content`에 inline Part를 그대로 수용하는지도 확인 필요

---

### Task 11. Cache stampede dedup — 3계층 Redis SETNX standby

#### 배경

v0.1.0은 라이브러리 구조라 dedup 개념 자체가 없었다. v0.2.0은 여러 에이전트가 Service에 동시에 같은 요청을 보낼 수 있는 환경으로 바뀌었는데, 이때 다음 3계층에서 **중복 작업**이 동시에 발생할 수 있다.

| 계층 | 중복 시 발생하는 비용 |
|------|-------------------|
| **files_api** (Gemini Files API 업로드) | 같은 파일을 동시에 N번 업로드 — Gemini API quota·시간 낭비 |
| **context_cache** (Context Cache 생성) | 동일 파일 조합으로 N개 Context Cache 생성 — **N배 저장비 발생** |
| **agent_llm** (최종 LLM 호출) | 동일 입력으로 N번 LLM 호출 — N배 토큰 비용 |

3계층 모두 **첫 요청이 완료되면 나머지는 그 결과만 재사용**하면 되는 구조라 dedup 도입이 필수.

#### 목적

- 동일 리소스를 만드는 작업을 **한 번만 수행** (leader-waiter 패턴)
- waiter는 exponential backoff로 대기하며 주기적으로 결과 조회
- leader가 `standby_ttl` 내에 완료 못하면 waiter가 **직접 작업 시작** (Y' Hybrid — 진행 우선)

#### 코드 세부 설명

##### standby 키 구조 (`service/app/logic/file_handler/dedup.py`)

각 계층이 자체 namespace의 Redis 키를 쓴다.

| 계층 | Redis 키 패턴 |
|------|---------------|
| files_api | `file_cache:files_api_standby:{checksum}` |
| context_cache | `file_cache:context_cache_standby:{hash_key}` |
| agent_llm | `agent_llm_standby:{cache_key}` |

Redis `SETNX`(not-exists 시에만 set)로 leader/waiter 구분. TTL = `standby_ttl` (기본 600초).

##### leader / waiter 흐름

```
try_acquire_standby(layer, key, ttl):   # SETNX
    acquired = SETNX ...
    returns True if leader else False

if leader:
    try:
        ... 실제 작업 (업로드 / 캐시 생성 / LLM 호출 + 저장) ...
    finally:
        release_standby(layer, key)     # DEL

else:  # waiter
    wait_for_standby_release(
        layer, key, timeout=standby_ttl,
        find_cache_fn=lambda: <해당 계층 캐시 조회>,
    )
    # exponential backoff: 0.5s → 1s → 2s → 4s → 최대 10s cap
    # 주기적으로 find_cache_fn() 호출 — HIT이면 즉시 복귀
    # standby 키 삭제 감지 시에도 즉시 복귀
    # timeout 초과 시 waiter가 직접 작업 수행 (Y' Hybrid)
```

##### lifespan과의 연결 (Task 1 참조)

Service가 leader 작업 도중 **crash**로 종료되면 standby 키가 Redis에 남아 후속 요청들이 무의미한 대기를 하게 된다. 이를 막기 위해 [Task 1의 `_flush_stale_standbys()`](pull_request_v2.md#task-1-sdk--service-분리--환경변수-일원화)가 startup 시점에 `file_cache:*_standby:*` + `agent_llm_standby:*` 패턴을 일괄 정리한다.

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/logic/file_handler/dedup.py` | `try_acquire_standby` / `release_standby` / `wait_for_standby_release` |
| 변경 | `service/app/logic/file_handler/resolver.py` | files_api 업로드 + context_cache 생성 경로에 dedup 적용 |
| 변경 | `service/app/logic/runner.py` | agent_llm 계층 dedup — cache miss 시 leader 선점 후 LLM 호출, waiter는 MDB 조회 |

#### 영향 범위

- **동시 중복 작업 제거**: Gemini 업로드·Context Cache 생성·LLM 호출 모두 동일 입력에 대해 1회만 수행
- **`standby_ttl` 조정 필요**: 작업 시간이 긴 요청(대용량 PDF·무거운 모델)에는 기본 600초가 짧을 수 있음. 사용자가 `timings.standby_ttl`로 조정 (예: 1200초)
- **Y' Hybrid 정책 주의**: leader가 timeout 초과 시 waiter가 직접 처리 → **서로 다른 응답**이 생길 가능성 있음 (LLM 비결정성). MDB에는 나중에 저장되는 쪽이 덮어씀

#### 연관 Task

- Task 1 (SDK + Service 분리): lifespan의 stale standby 정리와 쌍
- Task 6 (Redis 필수화): SETNX 기반이라 Redis 필수
- Task 9 (Files API URI 캐시): files_api 계층 dedup의 대상
- Task 10 (Context Cache opt-in): context_cache 계층 dedup의 대상
- Task 2 (SDK 파라미터 정리): `timings.standby_ttl`이 본 메커니즘의 조정 파라미터

---

### Task 12. 에러 타입 계층 (Service + SDK 양쪽)

#### 배경

v0.1.0은 라이브러리 구조라 내부에서 발생하는 예외(`llm_handler` 예외, `mdb_handler` 예외 등)가 호출자에게 그대로 전파됐다. 특별한 분류 계층이 필요 없었음.

v0.2.0에서 SDK + Service로 분리되면서(Task 1) **HTTP 경계**가 새로 생겼고, 다음 두 변환 계층이 필요해졌다.

1. Service 내부 예외 → HTTP 응답 (status + `error_type`)
2. SDK가 받은 HTTP 에러 응답 → Python 예외

#### 목적

- Service 쪽에 **`LLMRequestServiceError` 베이스 + 5종 subclass**로 원인별 HTTP status / `error_type` 매핑
- SDK 쪽에 **`ServiceConnectionError` / `ServiceError`** 로 네트워크 오류 vs HTTP 에러를 구분
- 호출자가 `except ServiceError as e: e.error_type` 으로 원인별 분기 가능

#### 코드 세부 설명

##### Service 에러 계층 (`service/app/core/errors.py`)

| 예외 | HTTP status | `error_type` | 발생 조건 |
|------|-------------|--------------|-----------|
| `LLMRequestServiceError` (베이스) | — | — | subclass의 공통 부모 |
| `ValidationError` | 422 | `validation` | 요청 스키마 검증 실패 |
| `UnsupportedFileTypeError` | 422 | `unsupported_file_type` | Gemini 지원 외 MIME 타입 |
| `FileTooLargeError` | 422 | `file_too_large` | 단일 파일 2GB 초과 |
| `FileUploadError` | 502 | `file` | Files API 업로드 실패 |
| `CacheError` | 502 | `cache` | MDB 조회·저장 실패 |

`main.py`의 FastAPI `exception_handler`가 이 예외들을 잡아 `ErrorResponse` JSON으로 변환해 내려준다.

##### HTTP 에러 응답 형식 (`ErrorResponse`)

```json
{
  "error": "사용자용 에러 메시지",
  "detail": "상세 내용 (선택)",
  "error_type": "validation | cache | file | handler | ...",
  "request_id": "uuid"
}
```

`request_id`는 middleware가 요청 단위로 발급 + 로그에도 동일 값 남김 → 장애 시 서버 로그와 대조 가능.

##### SDK 예외 (`sdk/llm_request/exceptions.py`)

```python
class SDKError(Exception): ...                 # 베이스

class ServiceConnectionError(SDKError):        # httpx.ConnectError / TimeoutException
    """llm-request Service 자체에 연결 실패 (네트워크·타임아웃)."""

class ServiceError(SDKError):                  # HTTP 4xx / 5xx
    """Service가 에러 응답을 반환. status_code / error_type / message 속성."""
    status_code: int
    error_type: str | None
    message: str
```

`_client.py`가 HTTP 응답을 읽어 위 두 예외로 변환하므로, 호출자는 SDK 예외만 다루면 된다.

##### 호출자 예시

```python
from llm_request import llm_request_async, ServiceConnectionError, ServiceError

try:
    response, meta = await llm_request_async(llm_input, files=files)
except ServiceConnectionError:
    # Service 자체에 접근 못 함 — 재시도·사용자 알림
    ...
except ServiceError as e:
    if e.error_type == "file_too_large":
        ...  # 파일 축소 유도
    elif e.error_type == "cache":
        ...  # MDB 일시 장애, 재시도 고려
    else:
        raise
```

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `service/app/core/errors.py` | `LLMRequestServiceError` + 5종 subclass |
| 변경 | `service/app/main.py` | FastAPI `exception_handler` 등록 — 예외 → `ErrorResponse` 변환, request_id middleware |
| 신규 | `service/app/models/responses.py` (`ErrorResponse`) | 에러 JSON DTO |
| 신규 | `sdk/llm_request/exceptions.py` | `SDKError` / `ServiceConnectionError` / `ServiceError` |
| 변경 | `sdk/llm_request/_client.py` | HTTP 4xx/5xx · 네트워크 오류 → SDK 예외로 변환 |

#### 영향 범위

- **호출자의 원인별 분기 가능**: `e.error_type` 으로 재시도·사용자 알림·스킵 등 분기 작성 가능
- **장애 추적 용이**: 에러 응답의 `request_id`를 서버 로그와 대조해 상세 맥락 확보

#### 연관 Task

- Task 1 (SDK + Service 분리): HTTP 경계 생성의 직접 결과
- Task 7 (Service 엔드포인트): 에러 응답도 엔드포인트 계약의 일부
- Task 3 (파일 입력 패러다임 변경): 파일 유효성 422 에러(`unsupported_file_type` / `file_too_large`)의 발생 지점

#### 관련 문서

- [debugging-guide.md](../debugging-guide.md) — SDK → Service → LLM → MDB 전체 흐름에서 발생하는 로그·예외 패턴을 파일·라인 단위로 정리. 장애 발생 시 `request_id`와 함께 이 가이드를 대조하면 원인 파악이 빠름

---

### Task 13. `llm_handler` 신규 공개 API 재수출 동기화

#### 배경

v0.1.0 이후 llm-handler가 공개 API를 추가했는데 `llm_request` 측 재수출은 반영되지 않아, 에이전트가 신규 기능을 쓰려면 `llm_handler`를 따로 import해야 했다. 재수출 범위를 최신 llm-handler에 맞춰 동기화한다.

#### 목적

- llm-handler가 추가한 공개 API를 `llm_request`에서 같은 패턴으로 노출
- 기존 재수출 이름·모듈 접근 패턴은 그대로 유지

#### 코드 세부 설명

##### 신규 재수출 9개

| 분류 | 이름 |
|------|------|
| 호출 | `embed` |
| 파일 | `delete_file`, `get_file`, `list_files` |
| Context Cache | `get_cache`, `update_cache` |
| 임베딩 모델 | `EmbedResponse`, `EmbeddingStatistics`, `EmbeddingValue` |

사용 예시:

```python
from llm_request import embed, delete_file, get_cache

result = embed("텍스트", model="text-embedding-004")
info = get_cache(cache_name)
delete_file(file_name)
```

기존 v0.1.0 재수출(`call`·`stream`·`session`·`upload_file`·`create_cache`·`delete_cache`·`list_caches`·`configure`·`Session`·모델 8종·예외 3종)은 **변경 없이 유지**.

> **async 재수출 및 `llm_handler` 직접 import 강제** 문제는 리뷰 피드백 이슈라 별도 항목인 **D. Fix — Issue #441** 에서 정리.

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `sdk/llm_request/__init__.py` | llm_handler 재수출 목록에 신규 9개 추가, `__all__` 업데이트 |

#### 영향 범위

- 에이전트가 `from llm_request import embed` 등으로 신규 API를 직접 사용 가능
- 기존 코드 호환 100%

#### 연관 Task

- Task 1 (SDK + Service 분리): SDK의 공개 API 면적 결정의 연장
- **D. Fix — Issue #441**: 재수출 면적 정비의 다른 축(async / configure)을 함께 다룸

---

## C. 참고사항

### file-manager는 간이 구현 상태

본 PR의 `packages/file-manager/`는 llm-request 개발·테스트 목적으로 **동일한 로직을 간이로 구축한 버전**이다. 정식 file-manager 담당자가 완성하는 대로 **그 버전으로 교체 예정**.

그에 따라 SDK 함수명도 현재는 `acheck_cache` / `aupload_file` / `aget_file` 처럼 `a` prefix가 붙지만, 정식 구현은 **async-only**로 제공될 예정이라 prefix를 뗀 `check_cache` / `upload_file` / `get_file`로 확정됨 (담당자와 합의). 본 PR 문서의 설명은 정식 이름 기준.

관련 Task: Task 8.

### LLM 오류 응답은 MDB에 저장되지 않음 (정책 유지)

v0.1.0부터 유지되는 정책. `llm_handler`에서 예외가 발생하거나 오류 응답이 반환되면 `runner`는 `save_result`를 호출하지 않는다. 실패한 호출은 캐시 문서로 남지 않으며, 다음 요청 시 다시 LLM을 호출.

추후 "저장은 하되 캐시 조회 대상에서 제외" 형태로의 개선 여지는 있지만, 본 PR에서는 변경하지 않음.

### `orchestrator.py` 는 여전히 비활성

v0.1.0 README의 디렉터리 구조에 "**(현재 사용되지 않음)**"으로 표기됐던 `orchestrator.py`(`run_many` / `run_many_stream`)는 v0.2.0에서도 같은 상태를 유지한다. 파일 자체는 `service/app/logic/orchestrator.py`로 이전됐으나 어떤 엔드포인트·러너도 이를 호출하지 않음. 테스트만 존재.

향후 배치 요청 유즈케이스가 구체화되면 재평가 예정.

## D. Fix

### Issue #441 — async 함수 재수출 누락 + `llm_handler` 직접 import 강제

#### 리뷰 지적 요약

- v0.1.0 `llm_request`는 sync 함수만 재수출했지 async 경로(`aio.call` / `aio.stream` 등)는 없어, 에이전트가 `llm_handler.aio`를 **별도로 import**해야 했음
- 이 비대칭으로 인해 에이전트가 자주 `llm_handler`를 직접 import하게 됐고, 이는 `llm_request`가 "`llm_handler`를 감싸서 단일 진입점으로 노출한다"는 설계 방향에 어긋남

#### 작업 중 병행 발견 — `configure` 이름 충돌

위 리뷰 지적을 해결하는 과정에서, `llm_request`와 `llm_handler` 양쪽에 `configure`라는 동일한 이름이 존재할 여지가 있어 잠재적 이름 충돌이 발견됨. Issue #441에서 지적된 내용은 아니지만 재수출 면적을 정비하는 김에 본 PR에서 함께 정리한다.

#### 해결

##### 1. `llm_request.aio` 서브모듈 신설

`llm_handler.aio`를 통째로 투명 재수출하는 모듈 추가.

```python
# sdk/llm_request/aio.py
from llm_handler import aio   # llm_handler.aio 전체 노출
```

사용 예:

```python
from llm_request import aio

response = await aio.call("안녕", model="gemini-2.5-flash")
async for chunk in aio.stream("질문"):
    ...
result = await aio.embed("텍스트")
```

이제 `from llm_handler.aio import ...` 직접 import는 더 이상 필요 없음.

##### 2. `configure` 이름 충돌 정리

`llm_request`도 향후 자체 `configure` 함수를 가질 여지가 있어 이름 충돌 가능성이 있었으나, **현재 시점에서 `llm_request`의 `configure`는 외부에서 쓸 일이 없음**. 따라서 `from llm_request import configure` 가 **`llm_handler.configure`를 노출하도록** 재수출 경로 정리. 추후 `llm_request`가 자체 `configure`를 갖게 되면 이 결정을 재검토.

##### 3. 기존 모듈 접근 경로 호환 유지

```python
from llm_request import llm_handler

llm_handler.call(...)
await llm_handler.aio.call(...)
```

v0.1.0의 "방법 2: 모듈 접근" 패턴도 그대로 작동.

#### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 신규 | `sdk/llm_request/aio.py` | `llm_handler.aio` 투명 재수출 |
| 변경 | `sdk/llm_request/__init__.py` | `aio` 서브모듈 export, `configure` 재수출 경로 `llm_handler.configure`로 확정 |

#### 영향 범위

- **import 경로 단순화**: 에이전트가 `llm_handler`를 직접 import할 이유가 거의 사라짐
- **`configure` 호환**: 현재 호출부는 `llm_handler.configure`가 실행되므로 동작 동일. 추후 `llm_request` 자체 `configure` 도입 시 재검토

#### 연관 Task

- Task 1 (SDK + Service 분리): `llm_request`가 단일 진입점이라는 설계 방향과 직결
- Task 13 (신규 재수출 동기화): 재수출 면적 정비의 다른 축과 짝

# 🧪 테스트 (Test)

### pytest — Service

```bash
cd packages/llm-request/service
uv run pytest -v
```

→ 전부 통과 ✅

### pytest — SDK

```bash
cd packages/llm-request/sdk
uv run pytest -v
```

→ 전부 통과 ✅

### 노트북 실전 검증

`quickstarts/verify_all.ipynb` 를 처음부터 끝까지 실행해 실 서비스 연동 경로를 수동 검증.

→ 전 셀 정상 실행 ✅

대상 커버리지:
- `llm_request_async` / `llm_request_stream_async` 단건·스트림 호출
- `done_check` / `fetch_by_cache_key` 캐시 조회
- `files=[FileInput]` 경로 (inline / Files API / PDF split 전부)
- Context Cache opt-in (`timings.context_cache_ttl`)
- MDB 저장 결과 직접 조회 (metadata / file_meta / expires_at)

# 📎 기타 (Etc) — 마이그레이션 가이드

v0.1.0 에이전트 코드를 v0.2.0으로 옮길 때 확인해야 할 항목.

### 체크리스트

- [ ] **SDK 설치 경로**: `packages/llm-request` → `packages/llm-request/sdk` (Task 1)
- [ ] **환경변수 일원화**: `MDB_HANDLER_URL` / `LLM_HANDLER_URL` / `MONGODB_DATABASE` 등 제거 → `LLM_REQUEST_URL` 하나만 (Task 1)
- [ ] **초기화 코드 전면 제거**: `MongoDBHandler.reset()`, `llm_handler.configure()`, `llm_handler.aio.configure()` 모두 삭제 (Task 1)
- [ ] **Service 별도 배포**: port 8101에 Service 기동 필수 (`uv run uvicorn app.main:app --port 8101`). **Redis 미연결 시 Service 자체가 뜨지 않음** (Task 1, 6)
- [ ] **파라미터 변경**: `ttl_seconds=...` → `timings={"mdb_ttl": ...}`, `as_stream` 제거 (Task 2)
- [ ] **meta 접근 방식**: `meta["source"]` → `meta.source` (dict → `RequestMeta` BaseModel) (Task 2)
- [ ] **파일 인자**: `contents=[..., FileRef]` → `files=[FileInput]` (Task 3)
- [ ] **MDB 직접 조회**: `doc["meta"]` → `doc["metadata"]`, `doc["elapsed_ms"]` 제거 (Task 5)
- [ ] **v0.1.0 캐시 문서는 재사용 불가**: 배포 초기 cache miss·LLM 재호출 비용 증가 예상 (Task 4)

### 최소 Before / After

Before (v0.1.0)
```python
import os
os.environ["MDB_HANDLER_URL"] = "http://..."
os.environ["LLM_HANDLER_URL"] = "http://..."
os.environ["MONGODB_DATABASE"] = "kw_ai"

from mdb_handler import MongoDBHandler; MongoDBHandler.reset()
import llm_handler; llm_handler.configure()
import llm_handler.aio; llm_handler.aio.configure()

from llm_request import llm_request_async
file_ref = llm_handler.upload_file("report.pdf")
response, meta = await llm_request_async(
    {"contents": ["요약", file_ref], "model": "gemini-2.5-flash"},
    ttl_seconds=3600,
)
print(meta["source"])
```

After (v0.2.0)
```python
# LLM_REQUEST_URL=http://192.7.9.45:8101 기본값 내장 — 다를 때만 export
from pathlib import Path
import mimetypes
from llm_request import llm_request_async, FileInput

p = Path("report.pdf")
fi = FileInput(
    file_name=p.name, file_size=p.stat().st_size,
    file_mtime=int(p.stat().st_mtime),
    mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
    extension=p.suffix.lstrip("."),
    data=p.read_bytes(),
)
response, meta = await llm_request_async(
    {"contents": "요약", "model": "gemini-2.5-flash"},
    files=[fi],
    timings={"mdb_ttl": 3600},
)
print(meta.source)
```
