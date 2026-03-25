# LLM Request

`llm_handler` SDK 위에 **MongoDB 캐시 + 병렬 처리**를 얹은 레이어입니다.
에이전트에서 LLM을 호출할 때 캐시 조회/저장/병렬 실행을 `state` dict 하나로 처리합니다.

> **Quickstart**: [quickstart.ipynb](quickstart.ipynb) — 실행 가능한 예시 코드

## 설치

```bash
pip install -e ./packages/llm-request
```

## 사전 조건

- LLM Handler 서비스 실행 중 (`http://localhost:8100`)
- MongoDB 접근 가능

```python
import os
os.environ["MONGODB_URI"] = "mongodb://192.7.9.45:27017/"
os.environ["MONGODB_DATABASE"] = "kw_ai"
os.environ["LLM_HANDLER_URL"] = "http://localhost:8100"

import llm_handler
llm_handler.configure()

import llm_handler.aio
llm_handler.aio.configure()
```

> **데이터베이스 안내**
>
> | 환경 | `MONGODB_DATABASE` | 용도 |
> |------|-------------------|------|
> | 개발/테스트 | `kw_ai_dev` | Quickstart, 기능 테스트, 프롬프트 실험 |
> | 운영/에이전트 | `kw_ai` | 실제 에이전트 배포 시 사용 |
>
> 개발 과정에서 불필요한 데이터가 DB에 쌓이지 않도록:
> - `save_mode=False`로 설정하여 DB 저장 자체를 끄거나
> - `cache_mode=False, save_mode=False`로 설정하여 캐시 조회/저장 모두 비활성화
>
> ```python
> # 개발 중: 저장 없이 LLM만 호출
> response, meta = llm_request({
>     "llm_input": {"input": "테스트 질문", "model": "gemini-3-flash-preview"},
>     "cache_mode": False,
>     "save_mode": False,
> })
> ```

## API

### `llm_request(state, *, database=None) -> tuple[LLMResponse, dict]` — sync 단건

```python
from llm_request import llm_request

response, meta = llm_request({
    "llm_input": {
        "input": "대한민국의 수도는?",
        "model": "gemini-3-flash-preview",
    },
})
print(response.text)        # "서울"
print(meta["source"])       # "live" 또는 "cache"
print(meta["cache_hit"])    # True / False
```

### `llm_request_async(state, *, database=None) -> tuple[LLMResponse, dict]` — async 단건

```python
from llm_request import llm_request_async

response, meta = await llm_request_async({
    "llm_input": {"input": "질문", "model": "gemini-3-flash-preview"},
})
```

`llm_request`와 동일한 기능의 async 버전입니다.
복합 파이프라인(LLM 여러 번 순차 호출)에서 `await`로 이벤트 루프에 양보하여
다른 작업과 병렬 실행할 때 사용합니다.

### `llm_request_stream(state, *, database=None) -> tuple[Iterator[StreamChunk], dict]` — sync 스트리밍

```python
from llm_request import llm_request_stream

stream, meta = llm_request_stream({
    "llm_input": {"input": "질문", "model": "gemini-2.5-flash"},
})

for chunk in stream:
    print(chunk.text, end="", flush=True)
print(meta["source"])  # "live" 또는 "cache"
```

캐시 히트/미스에 관계없이 항상 `tuple[Iterator[StreamChunk], dict]`를 반환합니다.
캐시 히트 시에는 캐시된 텍스트가 단일 StreamChunk로 감싸져 iterator에 담깁니다.
stream 완료 후 누적 텍스트가 자동으로 MongoDB에 저장됩니다.

### `llm_request_stream_async(state, *, database=None) -> tuple[AsyncIterator[StreamChunk], dict]` — async 스트리밍

```python
from llm_request import llm_request_stream_async

stream, meta = await llm_request_stream_async({
    "llm_input": {"input": "질문", "model": "gemini-2.5-flash"},
})

async for chunk in stream:
    print(chunk.text, end="", flush=True)
print(meta["source"])  # "live" 또는 "cache"
```

`llm_request_stream`의 async 버전입니다. `await`로 호출하고 `async for`로 chunk를 순회합니다.
캐시 히트/미스에 관계없이 항상 동일한 tuple 형태를 반환합니다.

### `run_many(states, *, concurrency=5, database=None) -> AsyncIterator[tuple[LLMResponse, dict]]` — 병렬 N건

```python
from llm_request import run_many

states = [
    {"llm_input": {"input": "질문1", "model": "gemini-3-flash-preview"}, "fund_code": "005930"},
    {"llm_input": {"input": "질문2", "model": "gemini-3-flash-preview"}, "fund_code": "000660"},
    # ... N개
]

async for response, meta in run_many(states, concurrency=10):
    code = meta["_state"]["fund_code"]
    print(f"[{code}] {response.text[:50]}")
```

- `concurrency`: 동시 실행 최대 수 (`asyncio.Semaphore`)
- 완료되는 순서대로 yield (`asyncio.as_completed`)
- 각 결과의 meta dict에 `_index` (입력 순서, 0부터)와 `_state` (원본 state)가 첨부됨

입력 순서대로 정렬이 필요하면:

```python
all_results = [(r, m) async for r, m in run_many(states)]
all_results.sort(key=lambda x: x[1]["_index"])
```

### `run_many_stream(states, *, concurrency=5, database=None) -> AsyncIterator[dict]` — 병렬 N건 스트리밍

```python
from llm_request import run_many_stream

states = [
    {"llm_input": {"input": "질문1", "model": "gemini-2.5-flash"}},
    {"llm_input": {"input": "질문2", "model": "gemini-2.5-flash"}},
]

async for event in run_many_stream(states, concurrency=5):
    if event["_type"] == "chunk":
        print(f"[{event['_index']}] {event['chunk'].text}", end="")
    elif event["_type"] == "done":
        print(f"\n[{event['_index']}] 완료 (cache_hit={event['meta']['cache_hit']})")
    elif event["_type"] == "cache_hit":
        response, evt_meta = event["data"]
        print(f"[{event['_index']}] 캐시: {response.text[:50]}")
    elif event["_type"] == "error":
        print(f"[{event['_index']}] 에러: {event['error']}")
```

N개 요청을 동시에 stream으로 실행하고, 모든 요청의 chunk가 실시간으로 섞여서 도착합니다.
각 이벤트에 `_index`가 붙어 어느 요청의 chunk인지 식별 가능합니다.

**모든 이벤트에 `meta` dict가 포함**되므로, 이벤트 타입에 관계없이 `event["meta"]`로 동일하게 메타 정보에 접근할 수 있습니다.
같은 요청의 chunk/done 이벤트는 동일한 meta dict 참조를 공유하므로 메모리 오버헤드가 없습니다.

이벤트 타입:

| `_type` | 의미 | 데이터 |
|---------|------|--------|
| `chunk` | stream 텍스트 조각 | `chunk`: StreamChunk, `meta`: dict |
| `done` | 해당 index의 stream 완료 | `meta`: dict |
| `cache_hit` | 캐시 히트, 완성 결과 | `data`: `tuple[LLMResponse, dict]`, `meta`: dict |
| `error` | 해당 index 에러 발생 | `error`: Exception, `meta`: dict |

### `done_check(state, *, lookup_cache=True, database=None) -> DoneCheckResult` — 캐시 키/조회

```python
from llm_request import done_check

dc = done_check(state, lookup_cache=True)
if dc.cached:
    print("이미 처리됨:", dc.cached["result"]["text"][:50])
else:
    print("미처리, cache_key:", dc.cache_key)
```

## state 구조

```python
state = {
    "llm_input": {              # llm_handler.call()에 전달할 인자
        "input": "질문",        # 필수
        "model": "gemini-3-flash-preview",
        "system": "시스템 프롬프트",     # 선택
        "temperature": 0,              # 선택
        "max_tokens": 200,             # 선택
        "service_name": "my_service",  # 선택 (메타에 기록)
    },
    "cache_mode": True,         # 캐시 조회 여부 (기본 True)
    "save_mode": True,          # DB 저장 여부 (기본 True)
    "ttl_seconds": 3600,        # 캐시 만료 시간 (선택)
    "debug": False,             # route_reason 포함 (선택)
}
```

### 캐시 동작

| cache_mode | save_mode | 동작 |
|---|---|---|
| `False` | `False` | LLM만 호출, 저장 없음 |
| `False` | `True` | LLM 호출 + DB 저장 (캐시 조회 안 함) |
| `True` | `True` | 캐시 조회 → 미스면 LLM + 저장, 히트면 DB 반환 |

## 반환값

모든 함수는 `tuple[LLMResponse, dict]` 형태의 튜플을 반환합니다.

```python
response, meta = llm_request(state)

# response: LLMResponse — .text, .model, .usage 등
# meta dict 구조:
{
    "source":     "live" | "cache",
    "cache_hit":  bool,
    "cache_key":  str | None,        # SHA-256 (64자)
    "meta":       dict,              # {service_name, model, provider}
    "collection": str | None,        # MongoDB 컬렉션명 (agent_llm)
}
```

`run_many` 사용 시 meta dict에 추가 필드:

```python
{
    "_index": int,    # 입력 순서 (0부터)
    "_state": dict,   # 원본 state 참조
}
```

`debug=True` 시 meta dict에 추가:

```python
{
    "route_reason": str,  # 모델 라우팅 사유
}
```

### stream 반환값

`llm_request_stream` / `llm_request_stream_async`는 캐시 히트/미스에 관계없이
항상 동일한 tuple을 반환합니다.

| 함수 | 반환 타입 |
|------|-----------|
| `llm_request_stream` | `tuple[Iterator[StreamChunk], dict]` |
| `llm_request_stream_async` | `tuple[AsyncIterator[StreamChunk], dict]` |

캐시 히트 시에는 캐시된 텍스트가 단일 StreamChunk로 감싸져 iterator에 담깁니다.
`isinstance` 분기 없이 동일한 코드로 처리할 수 있습니다.

`run_many_stream` 이벤트 구조 — **모든 이벤트에 `meta` 포함**:

```python
{"_index": int, "_type": "chunk",     "chunk": StreamChunk, "meta": dict}           # stream 텍스트 조각
{"_index": int, "_type": "done",      "meta": dict}                                  # 해당 index 완료
{"_index": int, "_type": "cache_hit", "data": (LLMResponse, dict), "meta": dict}    # 캐시 히트 (tuple)
{"_index": int, "_type": "error",     "error": Exception, "meta": dict}              # 에러 발생
```

## 에이전트 적용 패턴

### 패턴 A: 단순 N건 병렬 — `run_many`

펀드당 LLM 호출 1번, state를 미리 만들 수 있는 경우 (코멘트 생성, ESG DP 평가 등).

```python
states = [build_state(fund) for fund in funds]
async for response, meta in run_many(states, concurrency=10):
    process(response, meta)
```

### 패턴 B: 실시간 스트리밍 — `llm_request_stream` / `run_many_stream`

사용자에게 텍스트가 점진적으로 나타나는 UX가 필요한 경우.

```python
# 단건 stream
stream, meta = llm_request_stream(state)
for chunk in stream:
    append_to_ui(chunk.text)

# 다건 병렬 stream
async for event in run_many_stream(states, concurrency=5):
    route_to_ui(event["_index"], event)
```

### 패턴 C: 복합 파이프라인 — `llm_request_async` 직접

펀드당 LLM 여러 번 순차 호출, 이전 결과에 다음이 의존하는 경우 (등급 산정 등).

```python
async def evaluate_fund(fund):
    r1, m1 = await llm_request_async({"llm_input": {"input": classify_prompt(fund), ...}})
    r2, m2 = await llm_request_async({"llm_input": {"input": grade_prompt(r1, fund), ...}})
    return calculate_grade(r2)

sem = asyncio.Semaphore(10)
tasks = [asyncio.create_task(limited(f)) for f in funds]
for coro in asyncio.as_completed(tasks):
    result = await coro
```

> RM 위험등급 산정 시뮬레이션 전체 코드: [quickstart.ipynb — Appendix](quickstart.ipynb#Appendix:-RM-위험등급-산정-시뮬레이션)

---

> 상세 설계: [DESIGN.md](llm_request/DESIGN.md)
