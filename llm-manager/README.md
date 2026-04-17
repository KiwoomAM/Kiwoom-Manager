# 1. 개요

---

`llm_handler` SDK 위에 **MongoDB 캐시**를 얹은 레이어로
Agent에서 LLM을 호출할 때 캐시 조회/저장을 처리

- **Quickstart**: `quickstarts/quickstart_sdk.ipynb` — 간단한 예시 실행 가능한 코드

# 2. 핵심 특징

---

| 항목 | 설명 |
| --- | --- |
| 패키지명 | `kiwoom-llm-request` |
| 버전 | `0.1.0` |
| Python | **`>=3.11`** |
| 의존성 | mdb_handler, llm_handler, redis_handler |
| 통신 방식 | 패키지 설치,  **LLM Handler·MDB Handler 각각 HTTP(httpx)**. 캐시는 **MDB Handler 서비스** 경유 |

# 3. 디렉터리 구조

```
llm_request/
├── __init__.py              # 공개 API (llm_request*, run_many*, done_check)
├── runner.py                # 핵심: _prepare → done_check → call → save
├── keying.py                # 캐시 키 생성 (canonicalize → sha256)
├── checksum.py              # 파일 SHA-256 checksum + 메타정보
├── done_check.py            # MDB 캐시 조회
├── store.py                 # MDB 결과 저장
├── meta.py                  # 메타데이터 구성
├── orchestrator.py          # run_many, run_many_stream (배치 처리)
└── file_handler/            # 파일 업로드/캐싱 자동화 → 8. File Handler 참고
```

# 4. 설정 및 준비사항

- LLM Handler 서비스 실행 중 (`http://192.7.9.45:8100`)
- MongoDB 접근 가능 (`mongodb://192.7.9.45:27017/`)
- Redis 접근 가능 (파일 캐싱용, 미연결 시 메모리 fallback)

```bash
# 패키지 설치
pip install -e ./packages/llm-handler/sdk
pip install -e ./packages/mdb-handler/sdk
pip install -e ./packages/redis-handler
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

# 텍스트만
response, meta = await llm_request_async(
    {
        "contents": "대한민국의 수도는?",
        "model": "gemini-2.5-flash",
        "temperature": 0,
        "max_tokens": 200,
    },
)
print(response.text)
print(f"source: {meta['source']}")  # 'live' 또는 'cache'
```

```python
# 파일 포함 — file_paths만 넘기면 크기에 따라 자동 처리
response, meta = await llm_request_async(
    {"contents": "이 PDF를 요약해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
```

# 6. llm_request function 상세설명

---

| 함수 | 파라미터 | 반환 타입 | **설명** |
| --- | --- | --- | --- |
| `llm_request` | `llm_input: dict`, `*`, `file_paths=None`, `cache_mode=True`, `save_mode=True`, `ttl_seconds=None`, `debug=False`, `database=None`, `context_cache_ttl=None` | `tuple[LLMResponse, dict]` | **동기 단건 LLM 호출**. 캐시 조회 후 미스면 `llm_handler.call`, 히트면 캐시 결과 반환. |
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
        "service_name": "my_service",   # 선택 (메타에 기록). 레포 이름과 동일하게 지정 권장 ex. "kiwoom-rm", "kiwoom-esg"
        "tool_config": "auto",          # 선택 ("auto" | "any" | "none")
        "media_resolution": "high",     # 선택 ("low" | "medium" | "high" | "ultra_high")
    },
    file_paths=["report.pdf"],    # 파일 사용 시 경로 리스트. 텍스트만이면 생략 가능
    cache_mode=True,              # 기본 True
    save_mode=True,               # 기본 True
    ttl_seconds=3600,             # MDB 문서 만료 (기본 None)
    context_cache_ttl=None,       # Gemini Context Cache TTL (초). None이면 context caching 안 함
    debug=False,                  # 기본 False
    # database=None               # 기본 None → env MONGODB_DATABASE 또는 kw_ai
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
    service_name: str | None = None,    # 서비스 식별자 (로깅용). 레포 이름과 동일하게 지정 권장 ex. "kiwoom-rm", "kiwoom-esg"
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
    "metadata":   dict,          # {service_name, model}
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

## fetch_by_cache_key function 상세 설명

---

> 이미 `done_check`으로 받은 `cache_key`를 가지고 있을 때, `llm_input` 재구성 없이 **cache_key만으로** 캐시 결과를 조회하여 `llm_request`와 동일한 형식으로 반환

`fetch_by_cache_key(cache_key, *, database=None)`

- `database`: DB명 (기본 `kw_ai`)
- 반환: `(LLMResponse, meta)` — `llm_request`와 동일
- 캐시 키에 해당하는 문서가 없으면 `KeyError` 발생

```python
from llm_request import done_check, fetch_by_cache_key

# ① done_check으로 캐시 키 확보
dc = done_check(
    {"contents": "대한민국의 수도는?", "model": "gemini-3-flash-preview"},
    lookup_cache=False,
)
cache_key = dc.cache_key  # 이 키를 클라이언트가 보관

# ② 나중에 cache_key만으로 결과 조회
response, meta = fetch_by_cache_key(cache_key)
print(response.text)
print(meta["source"])  # "cache"
```

### Hashing(내부 함수로 외부에서 사용할 수 없음)

---

### 1. `llm_input`에서 일부 필드만 뽑음

캐시 키는 **`llm_input` dict의 아래 필드만** 사용함.

| 필드 | 설명 |
|------|------|
| `contents` | 필수. 질문/프롬프트 |
| `system` | 시스템 프롬프트 |
| `history` | 이전 대화 이력 |
| `model` | 모델명 |
| `max_tokens` | 최대 출력 토큰 수 |
| `temperature` | 온도 |
| `cached_content` | Context Cache 이름 |
| `service_name` | 서비스 식별자 |
| `seed` | 재현성 시드값 |
| `output` | 출력 형식 |
| `schema` | 출력 스키마 |
| `thinking` | 사고 수준 |
| `tools` | 도구 정의 |
| `tool_config` | 도구 설정 |
| `media_resolution` | 미디어 해상도 |
| `file_checksums` | 파일 checksum 리스트 (file_paths에서 자동 생성) |

이 목록에 **없는 필드는 캐시 키에 반영되지 않음**.

---

### 2. subset 전처리 — `llm_input`의 최상위 필드 대상

`build_llm_input_subset()`에서 위 필드를 추출하고, 값이 `None`이거나 키가 없으면 `""`로 치환.

```python
llm_input = {"contents": "안녕", "temperature": None}

# 결과 subset:
{
    "contents": "안녕",       # 값 있음 → 그대로
    "temperature": "",        # None → "" 치환
    "model": "",              # 키 없음 → "" 치환
    "seed": "",               # 키 없음 → "" 치환
    ...                       # 나머지 필드도 모두 "" 포함
}
```

---

### 3. 정규화 (canonicalize) — 각 필드 값의 내부 데이터 대상

`canonicalize_llm_input()`에서 각 필드 값 내부의 dict, list, float 등을 재귀적으로 정규화함.

```python
# tools 필드 값 내부의 데이터가 정규화 대상
"tools": [
    {
        "name": "search",
        "timestamp": "2026-04-02",   # ← volatile 키 → 제거
        "config": {
            "timeout": None,          # ← None → ""
            "rate": 0.30000001,       # ← float → 0.3
        }
    }
]
```

#### dict

- 키를 문자열로 변환
- 값 정규화 후 `None`이면 `""`로 치환
- 아래 volatile 키는 제거 — **요청마다 달라지는 메타데이터**이므로, 포함하면 같은 내용의 요청도 다른 해시가 됨

| 키 | 성격 |
|----|------|
| `timestamp`, `ts`, `created_at`, `updated_at` | 시간 정보 |
| `request_id`, `trace_id` | 요청 추적용 고유 ID |

#### list / tuple

- 각 원소에 정규화를 재귀 적용
- **기본값은 순서 유지** — `["A", "B"]` ≠ `["B", "A"]` (다른 해시)
- 옵션(`preserve_list_order=False`)으로 순서 무시 가능

> `contents`나 `history`는 메시지 순서가 중요하므로 기본값(순서 유지)이 적합.
> 태그 목록 같이 순서가 무의미한 경우에만 정렬 옵션 사용.

#### float

- 소수점 **4자리 반올림** — 부동소수점 미세 오차로 다른 해시가 되는 것 방지

```python
0.30000001 → 0.3    # 같은 해시
0.3        → 0.3    # 같은 해시
```

#### Decimal

- `Decimal.normalize()` 호출 후 문자열 변환 — 같은 값의 표현 차이를 통일

```python
Decimal("1.200") → "1.2"
Decimal("1.2")   → "1.2"   # 같은 해시
```

#### datetime / date

- `isoformat()` 문자열 변환 — JSON 직렬화 불가 타입이므로 고정 형식 문자열로 변환

#### bytes / bytearray

- hex 문자열 변환 — JSON 직렬화 불가 타입이므로 변환 (`b'\xff\x00'` → `"ff00"`)

#### str / int / bool / None

- 그대로 사용 (JSON 표준 타입)
- 단, dict 내부에서는 `None` → `""` 치환됨

#### 기타 타입

- `str(obj)` 시도, 실패하면 `""` — 예상 못 한 타입이 들어와도 해시 생성이 실패하지 않도록 하는 fallback

---

### 4. JSON 문자열 생성

정규화된 값을 `json.dumps()`로 직렬화.

- **dict 키의 알파벳 정렬** — 삽입 순서가 달라도 같은 결과 (리스트 원소 순서와는 무관)
- **JSON 구분자(`:`, `,`) 뒤의 포맷용 공백 제거** — 값 안의 공백은 그대로 유지

```python
{"contents":"안녕 하세요","model":"gpt-4"}
#                ↑ 값 내 공백은 유지
# ↑ 구분자 공백 없음

# 따라서 값 안의 공백 차이는 다른 해시:
"안녕하세요"  → 해시 A
"안녕하세요 " → 해시 B  (뒤에 공백 하나)
```

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

# ① subset: 15개 필드 전부 포함 + None/미입력 → ""
subset = build_llm_input_subset(llm_input)
print("subset:", subset)
# 예: {'contents': '수도는?', 'model': '...', 'system': '', 'temperature': 0.30000001, 'seed': '', ...}

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
| `file_meta` | 파일 checksum + 메타정보 리스트 (`file_paths` 전달 시에만) |
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

## `llm_request(llm_input, *, file_paths=None, cache_mode=True, save_mode=True, …) -> tuple[LLMResponse, dict]`

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

여러 건을 `asyncio.create_task`로 동시에 걸고 `asyncio.as_completed`로 먼저 끝난 순서대로 받는 패턴입니다. 아래 예시는 곧바로 LLM을 호출합니다. 항목마다 **DB 조회 지연을 시뮬**하고 로그로 비동기 겹침을 보고 싶다면 `quickstarts/quickstart_sdk.ipynb` 의 패턴B 코드를 참고하시기 바랍니다.

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

# 8. File Handler

---

file_handler는 llm_request의 하위 모듈로, `file_paths`가 전달되면 자동으로 동작한다.

```
llm_request/file_handler/
├── resolver.py      # 파일 크기 분기 (inline/Files API/split)
├── cache_store.py   # Redis CRUD (file_cache)
├── uploader.py      # Files API 업로드 + create_cache
└── splitter.py      # PDF 분할
```

## 파일 입력 종류

Client가 Gemini에 파일을 전달하는 모든 방식.

### A. `contents`에 직접 넣는 방식

| 방식 | 설명 | 사용 조건 |
|------|------|----------|
| **inline** | base64 인코딩하여 요청 body에 포함 | ≤20MB |
| **Files API URI** | Gemini Files API에 업로드 후 `file_uri`로 참조 | >20MB (PDF >50MB는 split 후 업로드) |
| **Open URI** | 외부 접근 가능한 URL을 직접 전달 (GCS, YouTube 등) | 공개 URL |

### B. `cached_content`

Gemini Context Cache에 파일을 미리 올려두고, 이후 요청에서 cache name으로 참조.
같은 파일로 반복 호출 시 입력 토큰 비용 90% 절감.

> `file_paths`를 넘기면 file_handler가 파일 크기에 따라 A의 방식을 자동 선택한다.
> `cached_content`는 `context_cache_ttl` 설정 시 opt-in으로 생성된다.
> Open URI는 현재 file_handler에서 미지원 — 필요 시 `contents`에 직접 `Part.from_uri()` 사용.

## 파일 처리 전략

| 조건 | 처리 방식 | 설명 |
|------|----------|------|
| ≤20MB | inline (base64) | 파일을 base64로 인코딩하여 요청에 포함 |
| >20MB | Files API 업로드 | Gemini Files API에 업로드 후 URI로 참조 |
| PDF >50MB | split → Files API | 페이지 단위로 분할 후 각 파트를 Files API에 업로드 |

## 전체 흐름

```
file_paths 입력
  → checksum 계산 (SHA-256)
  → 캐시 키 생성 (llm_input + file_checksums 해싱)
  → MDB 캐시 조회
    → hit: 캐시 반환
    → miss:
      → Redis에서 기존 context cache 조회
        → hit: cached_content 재사용
        → miss:
          → 파일 크기별 분기 (inline/Files API/split)
          → Files API 업로드 시 URI를 Redis에 캐싱 (48시간)
          → context_cache_ttl이 있으면 context cache 생성 + Redis에 저장
      → llm_handler로 LLM 호출
      → MDB에 결과 저장
```

## Phase 2: 서버화 후 아키텍처

현재(Phase 1)는 llm_request가 라이브러리로 동작하지만, Phase 2에서는 llm-handler와 합체하여 서버화된다.

### 현재 (Phase 1: 라이브러리)

```
Agent → llm_request (라이브러리, 같은 머신)
         ├→ MDB Handler (HTTP)
         ├→ Redis (직접 연결)
         ├→ file_handler (로컬 파일 접근)
         └→ llm-handler SDK → HTTP → llm-handler Service → Gemini API
```

### 서버화 후 (Phase 2)

```
Agent 머신                          별도 서버
┌────────────────┐                 ┌──────────────────────────┐
│  Agent          │                │  LLM Handler Service     │
│    └→ SDK       │── HTTP ──────→│    ├→ MDB (캐시)          │
│       - checksum│                │    ├→ Redis (파일 캐시)   │
│       - Redis   │                │    ├→ file_handler        │
│                 │                │    └→ Gemini API          │
└────────────────┘                 └──────────────────────────┘
```

### 핵심 차이: 파일 전송

| | SDK (Agent 머신) | Service (별도 서버) |
|--|------------------|-------------------|
| 파일 접근 | **가능** — 로컬 파일 직접 읽기 | **불가** — 파일이 없음 |
| checksum 계산 | **가능** — 파일 내용 해싱 | checksum을 **받기만** 함 |
| 파일 전송 | 서버로 파일을 보내지 않음 | Redis/Gemini에서 URI로 참조 |

> 서버화 후에도 파일 원본은 Agent 머신 → Gemini Files API로 직접 업로드되며,
> Service에는 checksum과 URI만 전달된다. Agent↔Service 간 파일 전송은 없다.

## Redis 캐시

file_handler는 Files API URI와 Context Cache name을 Redis에 캐싱한다.

| 카테고리 | 키 형식 | TTL | 용도 |
|---------|--------|-----|------|
| Files API | `file_cache:files_api:{checksum}` | 48시간 | 업로드된 파일 URI 재사용 |
| Context Cache | `file_cache:context_cache:{hash_key}` | 사용자 설정 | context cache name 재사용 |

- Redis 미연결 시 메모리 fallback (프로세스 종료 시 소멸)
- 같은 파일을 다시 업로드하지 않으므로 API 호출 횟수와 시간 절감

## Context Cache (opt-in)

같은 파일로 반복 호출할 때 `context_cache_ttl`을 설정하면 Gemini Context Cache를 생성한다.
캐시된 토큰은 입력 비용이 **90% 할인**된다.

```python
# 첫 호출: context cache 생성
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
    context_cache_ttl=3600,  # 1시간
)

# 이후 호출: context_cache_ttl 없어도 Redis에서 기존 캐시 자동 재사용
response2, meta2 = await llm_request_async(
    {"contents": "ESG 등급은?", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
```

> 같은 파일로 시간당 4회 이상 호출할 때만 사용 권장 (저장 비용 손익분기점)

# 9. 주의사항

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

### inline 파일(≤20MB) 다수 첨부 시 요청 크기 주의

≤20MB 파일은 base64로 인코딩되어 HTTP JSON body에 직접 포함됩니다. base64 인코딩은 원본 대비 약 33% 크기가 증가하므로, 20MB 파일 5개를 동시에 보내면 요청 body가 약 135MB에 달할 수 있습니다. 다수의 inline 파일을 첨부할 때는 총 크기를 고려하세요.

---

### 파일 포함 시 `file_paths` 필수

`file_paths`에 파일 경로를 넘기면 파일 크기에 따라 자동으로 처리됩니다. 내부 동작은 **8. File Handler** 참고.

```python
response, meta = await llm_request_async(
    {"contents": "이 PDF를 요약해줘", "model": "gemini-2.5-flash"},
    file_paths=["report.pdf"],
)
```

- `file_paths`가 있으면 파일 내용의 **SHA-256 checksum**으로 캐시 키를 생성
- 같은 파일 내용 → 같은 checksum → **캐시 히트** (재업로드, 다른 URI여도 무관)
- 파일 내용 변경 → 다른 checksum → **캐시 미스** (자동 재평가)
- **파일명은 캐시 키에 영향 없음** — 이름이 바뀌어도 내용이 같으면 캐시 히트
- 파일명, 크기, MIME 타입은 MongoDB에 `file_meta`로 저장되어 추적/조회 가능
- 텍스트만 사용 시 `file_paths` 생략 가능
- `contents`에 `FileRef`/`Part`/`cached_content`를 직접 넣지 않는다 — `file_paths`만 넘기면 내부에서 자동 처리

---

### LLM 오류 응답은 MDB에 저장되지 않음

`llm_handler`에서 예외가 발생하거나 오류 응답이 반환되면 `runner`는 `save_result`를 호출하지 않습니다.

즉, 실패한 호출은 캐시 문서로 남지 않으며, 다음 요청 시 다시 LLM을 호출합니다.

---

### 동시 호출 한계 (cache stampede dedup)

같은 input의 동시 요청은 Redis standby 메커니즘으로 중복 호출을 방지합니다:

- 첫 요청이 `standby_ttl_seconds`(기본 10분) 내에 완료되면 후속 요청은 자동으로 캐시 결과를 사용합니다.
- 첫 요청이 `standby_ttl_seconds` 내에 완료되지 않으면 후속 요청이 자기 작업을 시작합니다.
  - LLM 비결정성으로 두 요청이 다른 응답을 받을 수 있습니다.
  - 캐시(MDB)는 마지막 저장된 응답으로 유지됩니다.
- 작업 소요 시간에 맞게 `standby_ttl_seconds`를 조정하세요:
  - 짧은 작업: 120~300초
  - 대용량 + 무거운 LLM: 900~1200초

```python
response, meta = await llm_request_async(
    {"contents": "큰 PDF 분석", "model": "gemini-2.5-pro"},
    file_metas=file_metas,
    standby_ttl_seconds=1200,  # 20분 — 대용량 + pro 모델용
)
```

# 10. llm_handler API re-export

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


