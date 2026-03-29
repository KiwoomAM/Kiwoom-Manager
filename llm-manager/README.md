# 1. 개요

---

`llm_handler` SDK 위에 **MongoDB 캐시**를 얹은 레이어로
Agent에서 LLM을 호출할 때 캐시 조회/저장을 처리

- **Quickstart**: `quickstart.ipynb` — 간단한 예시 실행 가능한 코드

# 2. 핵심 특징

---

| 항목 | 설명 |
| --- | --- |
| 패키지명 | `kiwoom-llm-request` |
| 버전 | `0.1.0` |
| Python | **`>=3.11`** |
| 의존성 | mdb_handler, llm_handler |
| 통신 방식 | 패키지 설치,  **LLM Handler·MDB Handler 각각 HTTP(httpx)**. 캐시는 **MDB Handler 서비스** 경유 |

# 3. 디렉터리 구조

```python
packages/llm-request/llm_request/
├── __init__.py                   # 공개 API (llm_request*, run_many*, done_check)
├── runner.py                     # 단건 실행: sync/async + stream, 캐시·저장 연동
├── orchestrator.py               # run_many, run_many_stream (병렬 N건) (현재 사용되지 않음)
├── done_check.py                 # 캐시 키 생성·조회 (DoneCheckResult)
├── store.py                      # MongoDB 캐시 get/set (mdb_handler 연동)
├── keying.py                     # llm_input canonicalize → SHA-256 캐시 키
├── meta.py                       # llm_input → 저장용 meta dict (build_meta)
└── DESIGN.md                     # 설계 문서
```

# 4. 설정 및 준비사항

- LLM Handler 서비스 실행 중 (`http://192.7.9.45:8100`)
- MongoDB 접근 가능 (`mongodb://192.7.9.45:27017/`)

```bash
# 패키지 설치
pip install -e ./packages/llm-handler/sdk
pip install -e ./packages/mdb-handler/sdk
pip install -e ./packages/llm-request
```

```python
# 사전 정의
import os
os.environ["MDB_HANDLER_URL"] = "http://192.7.9.45:8300"  # MDB Handler HTTP
os.environ["LLM_HANDLER_URL"] = "http://192.7.9.45:8100"

# 개발/테스트
os.environ["MONGODB_DATABASE"] = "kw_ai_dev"
# 운영/에이전트
os.environ["MONGODB_DATABASE"] = "kw_ai"

from mdb_handler import MongoDBHandler
MongoDBHandler.reset()

import llm_handler
llm_handler.configure()

import llm_handler.aio
llm_handler.aio.configure()
```

> **데이터베이스 안내**
> 
> 
> 
> | 환경 | `MONGODB_DATABASE` | 용도 |
> | --- | --- | --- |
> | 운영/에이전트 | `kw_ai` | 실제 에이전트 배포 시 사용 |
> | 개발/테스트 | `kw_ai_dev` | Quickstart, 기능 테스트, 프롬프트 실험 |
> 
> 개발 과정에서 불필요한 데이터가 DB에 쌓이지 않도록:
> - `save_mode=False`로 설정하여 DB 저장 자체를 끄거나 dev 컬렉션에 저장하여 테스트 후 dev 컬렉션 정리 권장
> 
> ```python
> # 저장 없이 LLM만 호출
> response, meta = llm_request(
>     {"contents": "테스트 질문", "model": "gemini-3-flash-preview"},
>     cache_mode=False,
>     save_mode=False,
> )
> ```
> 

# 5. Quick Start

---

```python
from llm_request import llm_request_async

response, meta = await llm_request_async(
    {
        "contents": "대한민국의 수도는?",
        "model": "gemini-3-flash-preview",
        "temperature": 0,
        "max_tokens": 200,
    },
)
print(response.text)
print(f"source:{meta['source']}")  # 'live' 또는 'cache'
```

# 6. llm_request function 상세설명

---

| 함수 | 파라미터 | 반환 타입 | **설명** |
| --- | --- | --- | --- |
| `llm_request` | `llm_input: dict`, `*`, `cache_mode=True`, `save_mode=True`, `ttl_seconds=None`, `debug=False`, `database=None` | `tuple[LLMResponse, dict]` | **동기 단건 LLM 호출**. 캐시 조회 후 미스면 `llm_handler.call`, 히트면 캐시 결과 반환. |
| `llm_request_async` | 위와 동일 | `tuple[LLMResponse, dict]` | **비동기 단건 LLM 호출**. 캐시 조회 후 미스면 `llm_handler.aio.call`, 히트면 캐시 결과 반환. |
| `llm_request_stream` | 위와 동일 | `tuple[Iterator[StreamChunk], dict]` | **동기 스트리밍 LLM 호출**. 캐시 조회 후 미스면 `llm_handler.stream`, 히트면 캐시 결과 반환. |
| `llm_request_stream_async` | 위와 동일 | `tuple[AsyncIterator[StreamChunk], dict]` | **비동기 스트리밍 LLM 호출.** 캐시 조회 후 미스면 `llm_handler.aio.stream`, 히트면 캐시 결과 반환. |

## Input 구조

```python
response, meta = await llm_request_async(
    {                           # llm_input: llm_handler.call()에 전달할 인자 (키 생략 시 대부분 미전송)
        "contents": "질문",     # 필수. str 또는 [str | Part | FileRef] 혼합 리스트
        "model": "gemini-3-flash-preview",
        "system": "시스템 프롬프트",      # 선택
        "temperature": 0,               # 선택
        "max_tokens": 200,              # 선택
        "service_name": "my_service",   # 선택 (메타에 기록)
        "tool_config": "auto",          # 선택 ("auto" | "any" | "none")
        "media_resolution": "high",     # 선택 ("low" | "medium" | "high" | "ultra_high")
    },
    cache_mode=True,            # 기본 True
    save_mode=True,             # 기본 True
    ttl_seconds=3600,           # 기본 None (예시로 3600 지정)
    debug=False,                # 기본 False
    # database=None             # 기본 None → env MONGODB_DATABASE 또는 kw_ai
)
```

### a. llm_input

### `call(contents, ...) → LLMResponse`

단일 LLM 호출. `contents`만 필수, 나머지는 SDK 기본 `None`(요청 JSON에서 생략). `model` 생략 시 Handler 기본 `gemini-3-flash-preview`.

```python
def call(
    contents: ContentsType,  # 필수. str | list[str | FileRef | Part]
    *,
    system: str | None = None,          # 시스템 프롬프트
    model: str | None = None,           # 모델명 (기본: gemini-3-flash-preview)
    temperature: float | None = None,   # 0.0(결정적) ~ 2.0(창의적)
    max_tokens: int | None = None,      # 최대 출력 토큰 수
    seed: int | None = None,            # 재현성을 위한 시드값
    output: str | None = None,          # "json" | "enum" | "text"
    schema: type | dict | None = None,  # Pydantic / Enum / dict (output 자동 추론)
    thinking: str | None = None,        # "minimal" | "low" | "medium" | "high"
    tools: list[dict] | None = None,    # Function calling / Built-in 도구 정의
    tool_config: str | None = None,     # "auto" | "any" | "none"
    history: list[dict] | None = None,  # 이전 대화 이력
    cached_content: str | None = None,  # Context Cache 이름
    service_name: str | None = None,    # 서비스 식별자 (로깅용)
    media_resolution: str | None = None, # "low" | "medium" | "high" | "ultra_high"
) -> LLMResponse
```

---

### `stream(contents, ...) → Iterator[StreamChunk]`

스트리밍 LLM 호출. 파라미터는 `call()`과 동일. async 버전은 `llm_handler.aio.stream()`.

### b. cache_mode, save_mode

> **cache_mode = True(기본값)**
MDB의 cache존재 여부 체크
> 

> **save_mode = True(기본값)**
LLM응답 MDB에 저장
> 

| cache_mode | save_mode | 동작 |
| --- | --- | --- |
| `False` | `False` | LLM만 호출, 저장 없음 |
| `False` | `True` | LLM 호출 + DB 저장 (캐시 조회 안 함) |
| `True` | `True` | 캐시 조회 → 미스면 LLM + 저장, 히트면 DB 반환 |
| `True` | `False` | 캐시 조회 → 미스면 LLM, 히트면 DB 반환 |
| 저장은 하지 않음 |  |  |

### c. ttl_seconds

> MDB의 유효시간 설정 변수 설정 시간 지나면 자동 파기
ttl_seconds = None(기본값-유효기한 없음)
ttl_seconds = 3600(1시간 유효)
> 
- `ttl_seconds`를 주면 캐시 문서에 **`expires_at`** 이 저장됩니다. **만료 시각 이후 자동 삭제**를 하려면 MongoDB **TTL 인덱스**(또는 동일 필드를 보는 정리 작업)가 **MDB/DB 쪽에 설정되어 있어야** 합니다. SDK만으로 문서를 지우지는 않습니다.*

### d. debug

> 디버깅 용 설정
True 설정 시 응답이 구조의 어떤 루트로 출력된 건지 기록되어 있음
> 

## Output 구조

> 모든 함수는 `tuple[response, dict]` 형태의 튜플을 반환
> 

**cache_hit, miss 상관 없이 출력 양식 동일**

```python
response, meta = llm_request(
    {"contents": "질문", "model": "gemini-3-flash-preview"},
)

# response: LLMResponse — .text, .model, .usage 등
# meta dict 구조:
{
    "source":     "live" | "cache",
    "cache_hit":  bool,
    "cache_key":  str | None,    # SHA-256 (64자)
    "meta":       dict,          # {service_name, model}
    "collection": str | None,    # MongoDB 컬렉션명 (agent_llm)
    "route_reason": str,         # debug = True 시 추가됨
}
```

### a. `LLMResponse`

`call()` 및 `session.call()`의 반환 타입.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | `str | None` | 모델 응답 텍스트 (Tool calling 시 비어 있을 수 있음) |
| `model` | `str` | 사용된 모델명 |
| `usage` | `TokenUsage | None` | 토큰 사용량 |
| `finish_reason` | `str | None` | 종료 사유 (`"STOP"`, `"MAX_TOKENS"`, `"SAFETY"` 등) |
| `function_calls` | `list[FunctionCall] | None` | 모델이 요청한 함수 호출 |
| `request_id` | `str` | 요청 식별자 |

### b. `StreamChunk`

`stream()` 및 `session.stream()`이 yield하는 개별 청크.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | `str | None` | 청크 텍스트 |
| `finish_reason` | `str | None` | 마지막 청크에만 포함 |
| `usage` | `TokenUsage | None` | 마지막 청크에만 포함 |
| `function_calls` | `list[FunctionCall] | None` | 해당 청크에 포함된 함수 호출 |

## done_check function 상세 설명

---

> Hashing, 캐시 조회
> 

`done_check(llm_input, *, lookup_cache=True, database=None) -> DoneCheckResult`

```python
from llm_request import done_check

llm_input = {
    "contents": "대한민국의 수도는?",
    "model": "gemini-3-flash-preview",
    "temperature": 0,
}

dc = done_check(llm_input, lookup_cache=True)

print("collection:", dc.collection)       # 기본 "agent_llm"
print("cache_key:", dc.cache_key)         # 64자 hex
print("cache_error:", dc.cache_error)     # 조회 예외 시 True, cached는 None

if dc.cached is not None:
    # MDB 문서 그대로: result, meta, canonical_json 등
    print("캐시 히트, result 키:", list(dc.cached.get("result", {}).keys()))
    text = dc.cached.get("result", {}).get("text")
    print("text 앞부분:", (text or "")[:80])
else:
    print("캐시 미스 또는 조회 실패") # dc.cache가 None인 경우 조회 실패 혹은 cache 존재하지 않음
```

### Hashing(내부 함수로 외부에서 사용할 수 없음)

---

### 1. `llm_input`에서 일부 필드만 뽑음

캐시 키는 **`llm_input` dict의 일부 필드만** 사용함.

`_LLM_INPUT_FIELDS`:

- `contents`, `system`, `history`, `model`, `max_tokens`, `temperature`, `cached_content`, `service_name`, `seed`, `output`, `schema`, `thinking`, `tools`, `tool_config`, `media_resolution`

이 목록에 **없는 값은 캐시 키에 반영되지 않음**.

---

### 2. subset 만들 때 1차 전처리

`build_llm_input_subset()`에서:

- 1번 필드만 추출
- 값이 `None`이면 `""`로 치환
- 키가 아예 없으면 포함하지 않음

---

### 3. canonicalize에서 2차 정규화

`canonicalize_llm_input()`에서 캐시 키용 문자열로 만들기 전에 정규화함.

전처리 종류:

- **dict**
    - 키를 문자열로 변환
    - 아래 volatile 키는 제거
        - `timestamp`
        - `ts`
        - `created_at`
        - `updated_at`
        - `request_id`
        - `trace_id`
    - 값 정규화 후 `None`이면 `""`로 치환
- **list / tuple**
    - 각 원소 재귀 정규화
    - 기본값은 **순서 유지**
    - 옵션으로 순서 무시 정렬 가능
- **float**
    - 소수점 **4자리 반올림**
- **Decimal**
    - 정규화 후 문자열 변환
- **datetime / date**
    - `isoformat()` 문자열 변환
- **bytes / bytearray**
    - hex 문자열 변환
- **str / int / bool / None**
    - 그대로 사용
    - 단, dict 내부에서는 결과적으로 `None -> ""` 처리될 수 있음
- **기타 타입**
    - 가능하면 `str(obj)`
    - 실패하면 `""`

---

### 4. JSON 문자열 생성

정규화된 값을 `json.dumps()`로 직렬화.

- **키 정렬**
- **공백 없는 한 줄 JSON**
- **같은 입력이면 항상 같은 문자열**

---

### 5. SHA-256 해시 생성

문자열을 UTF-8로 인코딩해서 **SHA-256 hex 64자리 문자열**로 변환.

### **3) 예시 코드**

```python
# 원본 llm_input
llm_input = {
    "contents": "수도는?",
    "model": "gemini-3-flash-preview",
    "system": None,  # 전처리 후 "" 로 치환
    "temperature": 0.30000001,  # float는 기본 소수 4자리 반올림 → 0.3
    "max_tokens": 200,
    "service_name": "demo",
    # service_name 등 메타 전용 필드는 해시에 포함됨 (의도적 — 서비스별 캐시 분리)
}

# ① subset: 지정 필드만 + None → ""
subset = build_llm_input_subset(llm_input)
print("subset:", subset)
# 예: {'contents': '수도는?', 'model': '...', 'system': '', 'temperature': 0.30000001, ...}

# ② canonical JSON 문자열 (SHA 직전)
canon = canonicalize_llm_input(subset)

# ③ llm_input 한 번에 (done_check 내부와 동일 경로)
canon2 = canonicalize_input(llm_input)
assert canon2 == canon

# ④ 캐시 키(64자리 cache_key생성)
cache_key = make_cache_key(canon)
```

## MDB 저장 정보 상세설명

### **저장 방식**

- **컬렉션**: 기본 **`agent_llm`**
- **문서 `_id`**: 위에서 만든 **`cache_key`**(SHA-256 hex)와 동일.

### **MDB 문서 저장 필드**

| **필드** | **설명** |
| --- | --- |
| `_id` = `cache_key` | 조회·저장 키 |
| `canonical_json` | 캐시 키 계산에 쓰인 정규화 JSON 문자열 |
| `result` | LLM 결과 **dict** (`LLMResponse.model_dump()` 또는 Stream 병합 dict) |
| `meta` | `build_meta(llm_input)` — `service_name`, `model` |
| `created_at` | 저장 시각(UTC ISO) |
| `requested_at`, `elapsed_ms` | LLM 요청 시간, LLM 소요시간 |
| `expires_at` | `ttl_seconds`가 있을 때만 |

### **“완전 raw”에 가까운 저장**

- **비스트림**: 성공 시 **`LLMResponse`를 `model_dump()` 한 것**이 `result`에 들어갑니다. (필드명은 모델 스키마 따름 — 순수 문자열 하나만이 아니라 구조화된 dict.)
- **스트림** (`runner._finalize_stream`): 스트림이 **끝난 뒤** 청크 텍스트를 이어 붙여 **`result`에 dict로 저장**.
대략 `text`, `model`, `usage`(마지막 청크), `finish_reason`, `request_id`, `source: "stream"` 형태. Tool calling 스트림이면 마지막 비-None 청크의 `function_calls`를 `result`에 포함할 수 있음.
→ **마지막 청크가 온 뒤, 이터레이터가 끝나야** 저장이 호출. 중간 청크마다 MDB에 저장하지 않음.

### **오류 시 저장**

- 현재 **`runner`는 성공 경로에서만 `save_result` 호출.** **호출 실패·예외 응답은 이 레이어에서 캐시 문서로 남기지 않습니다.**
- 문구 예시:
    
    > *오류 응답/실패 케이스는 **현재 MDB에 저장하지 않으며**, 추후 필요 시 **저장은 하되 캐시 조회 대상에서 제외**하는 식으로 개선할 여지 있음.*
    > 

## Architecture

![image.png](attachment:5fad32ac-7f32-4d05-a2c5-c306141cc389:image.png)

## `llm_request(llm_input, *, cache_mode=True, save_mode=True, …) -> tuple[LLMResponse, dict]`

```python
from llm_request import llm_request

response, meta = llm_request(
    {
        "contents": "대한민국의 수도는?",
        "model": "gemini-3-flash-preview",
    },
)
print(response.text)        # "서울"
print(meta["source"])       # "live" 또는 "cache"
print(meta["cache_hit"])    # True / False
```

## `llm_request_async(llm_input, *, …) -> tuple[LLMResponse, dict]`

```python
from llm_request import llm_request_async

response, meta = await llm_request_async(
    {"contents": "질문", "model": "gemini-3-flash-preview"},
)
```

## `llm_request_stream(llm_input, *, …) -> tuple[Iterator[StreamChunk], dict]`

```python
from llm_request import llm_request_stream

stream, meta = llm_request_stream(
    {"contents": "질문", "model": "gemini-2.5-flash"},
)

for chunk in stream:
    print(chunk.text or "", end="", flush=True)
print(meta["source"])  # "live" 또는 "cache"
```

## `llm_request_stream_async(llm_input, *, …) -> tuple[AsyncIterator[StreamChunk], dict]`

```python
from llm_request import llm_request_stream_async

stream, meta = await llm_request_stream_async(
    {"contents": "질문", "model": "gemini-2.5-flash"},
)

async for chunk in stream:
    print(chunk.text or "", end="", flush=True)
print(meta["source"])  # "live" 또는 "cache"
```

# 7. 자주 사용하는 패턴

---

## 패턴 A: 단건 처리 — `llm_request_async`

```python
from llm_request import llm_request_async

response, meta = await llm_request_async(
    {"contents": "질문", "model": "gemini-3-flash-preview"},
)
print(response.text)
print(f"source:{meta['source']}")
```

## 패턴 B: N건 처리 — `llm_request_async`

여러 건을 `asyncio.create_task`로 동시에 걸고 `asyncio.as_completed`로 먼저 끝난 순서대로 받는 패턴입니다. 아래 예시는 곧바로 LLM을 호출합니다. 항목마다 **DB 조회 지연을 시뮬**하고 로그로 비동기 겹침을 보고 싶다면 `quickstart.ipynb` 의 패턴B 코드를 참고하시기 바랍니다.

```python
import asyncio
import time
from llm_request import llm_request_async

items = [
    {"id": "a", "prompt": "첫 번째 질문"},
    {"id": "b", "prompt": "두 번째 질문"},
]

async def process_item(item: dict, wall0: float) -> dict:
    t0 = time.perf_counter()
    response, meta = await llm_request_async(
        {
            "contents": item["prompt"],
            "model": "gemini-3-flash-preview",
            "max_tokens": 350,
        },
        cache_mode=True,
        save_mode=True,
    )
    llm_sec = time.perf_counter() - t0
    rel = time.perf_counter() - wall0
    print(f"[{rel:5.2f}s]{item['id']} 완료 (LLM{llm_sec:.2f}s, source={meta['source']})")
    return {"id": item["id"], "text": response.text, "llm_sec": llm_sec, "cache_key": meta["cache_key"]}

wall0 = time.perf_counter()
tasks = [asyncio.create_task(process_item(it, wall0)) for it in items]
results = [await c for c in asyncio.as_completed(tasks)]
```

# 8. 주의사항

---

### 스트림 함수는 이터레이터를 끝까지 소비해야 저장됨

`llm_request_stream` / `llm_request_stream_async`는 **모든 청크를 순회한 뒤** 마지막에 MDB에 저장합니다.
중간에 `break`하거나 이터레이터를 버리면 저장 호출이 실행되지 않습니다.

```python
stream, meta = llm_request_stream({"contents": "질문", "model": "gemini-2.5-flash"})
for chunk in stream:
    if some_condition:
        break  # ⚠️ 여기서 break하면 저장 안 됨
```

---

### `ttl_seconds`는 `expires_at` 필드만 기록 — 자동 삭제는 MongoDB TTL 인덱스 필요

`ttl_seconds`를 지정하면 문서에 `expires_at` 필드가 저장될 뿐, SDK 자체는 문서를 삭제하지 않습니다.
실제 만료 후 자동 삭제가 필요하면 MDB 측에 TTL 인덱스(`expires_at` 필드 대상)가 설정되어 있어야 합니다.

---

### 파일 포함 시 캐시 키 주의

`contents`에 `FileRef`를 넣으면 **파일 URI**가 캐시 키에 포함됩니다.

동일한 파일을 `upload_file()`로 **재업로드**하면 URI가 달라져 캐시 미스가 발생합니다.

- 동일 파일을 여러 번 사용하려면 `FileRef`를 재사용하거나 URI를 `Part.from_uri()`로 고정하세요.
- `Part.from_bytes()` / `Part.from_file()`의 경우 base64 hex가 캐시 키에 포함되므로 파일 내용이 같으면 동일 키가 생성됩니다.
- > 해당 부분은 다음 업데이트 시 pdf가 동일한 경우 동일한 cache_key가 될 수 있도록 개선할 예정입니다.

---

### LLM 오류 응답은 MDB에 저장되지 않음

`llm_handler`에서 예외가 발생하거나 오류 응답이 반환되면 `runner`는 `save_result`를 호출하지 않습니다.

즉, 실패한 호출은 캐시 문서로 남지 않으며, 다음 요청 시 다시 LLM을 호출합니다.

# 9. llm_handler API re-export

---

`llm_request` 패키지만 설치하면 `llm_handler`의 모든 public API를 별도 import 없이 사용할 수 있습니다.

## 방법 1: flat import

`llm_handler`의 함수·모델·예외를 `llm_request`에서 직접 import합니다.

```python
from llm_request import call, stream, Part, LLMResponse

# 동기 호출
response = call("안녕하세요")
print(response.text)

# 스트리밍
for chunk in stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)
```

### re-export 목록

| 분류 | 이름 |
| --- | --- |
| 함수 | `call`, `stream`, `session`, `upload_file`, `create_cache`, `delete_cache`, `list_caches`, `configure` |
| 클래스 | `Session` |
| 모델 | `Part`, `LLMResponse`, `StreamChunk`, `FunctionCall`, `CacheRef`, `FileRef`, `TokenUsage`, `ContentsType` |
| 예외 | `SDKError`, `HandlerConnectionError`, `HandlerError` |

## 방법 2: 모듈 접근

`llm_handler` 모듈 자체를 import하여 네임스페이스를 유지할 수 있습니다.

```python
from llm_request import llm_handler

# 동기
response = llm_handler.call("안녕하세요")

# 비동기 (llm_handler.aio 접근 가능)
response = await llm_handler.aio.call("안녕하세요")

# 설정
llm_handler.configure(base_url="http://192.7.9.45:8100")
llm_handler.aio.configure(base_url="http://192.7.9.45:8100")
```

## 두 방법 혼합

```python
from llm_request import llm_request_async, call, Part, llm_handler

# 캐시 있는 호출
response, meta = await llm_request_async(
    {"contents": "질문", "model": "gemini-3-flash-preview"},
)

# 캐시 없이 직접 호출
response = call("질문", model="gemini-3-flash-preview")

# 비동기 직접 호출
response = await llm_handler.aio.call("질문", model="gemini-3-flash-preview")
```