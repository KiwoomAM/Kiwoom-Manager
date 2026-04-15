# 동시성 dedup 설계 — 인메모리 single-flight (Approach 1)

> **상태**: 논의용 초안 (draft)
> **스코프**: 단일 replica 배포 환경 가정
> **관련**: [inline-vs-files-api-strategy.md](inline-vs-files-api-strategy.md) Phase A 보강 항목

---

## 1. 배경 / 문제 정의

llm-request는 비동기 서비스다. 에이전트가 **완전히 동일한 요청을 거의 동시에 N번** 보낼 수 있다. 이 때 두 계층에서 캐시 stampede가 발생한다.

### 1.1 시나리오

에이전트가 같은 PDF + 같은 프롬프트로 3개의 요청을 동시에 (`asyncio.gather`) 보냈다고 가정:

```
t=0ms   req A, B, C 모두 도착
t=1ms   A, B, C 각자 done_check → MDB 아직 비어있음 → 모두 cache miss
t=2ms   A, B, C 각자 Redis files_api:{checksum} 조회 → 모두 miss
t=3ms   A, B, C 각자 upload_file() 호출 → Gemini 업로드 3회 발생 ⚠️
t=10s   A, B, C 각자 create_cache() → context_cache 3개 생성 ⚠️
t=11s   A, B, C 각자 llm_handler.call() → LLM 호출 3회 발생 ⚠️
t=15s   A, B, C 각자 MDB save_result → 3회 덮어쓰기 (마지막이 이김)
```

### 1.2 각 계층별 피해

| 계층 | 중복 호출 | 피해 |
|------|----------|------|
| Files API 업로드 | 3회 | 불필요한 네트워크/업로드 비용 (Gemini 과금), URI 불일치 (Redis에 마지막만 남음) |
| context_cache 생성 | 3회 | **48시간 TTL 동안 중복 캐시 3개**. 토큰 저장 비용 3배 |
| LLM 호출 | 3회 | **토큰 비용 3배**. 체감 비용 가장 큼 |
| MDB save_result | 3회 | 덮어쓰기만 발생 (`replace_one` upsert). _id 충돌은 없음 |

**핵심 문제**: Gemini/토큰 비용이 N배로 증가한다. MDB 쓰기 자체는 이미 `replace_one(upsert=True)`으로 idempotent하지만, **그 앞 단의 비싼 호출**이 중복된다.

### 1.3 왜 지금 다루는가

- Phase A([inline-vs-files-api-strategy.md](inline-vs-files-api-strategy.md))가 `upload_file` → `create_cache` → `save_files_api` 파이프라인을 완성했지만 **dedup 고려가 빠져 있다**
- 운영 투입 전에 해결해두지 않으면 동시 호출 많은 에이전트(예: 배치 처리)가 바로 비용 폭증

---

## 2. 현재 코드 상태 (2026-04-15 기준)

| 위치 | 현재 동작 | dedup 여부 |
|------|----------|-----------|
| [cache_store.py:60](../service/app/logic/file_handler/cache_store.py#L60) `save_files_api` | `redis SET` (NX 아님) | ❌ |
| [cache_store.py:79](../service/app/logic/file_handler/cache_store.py#L79) `save_context_cache` | `redis SET` (NX 아님) | ❌ |
| [uploader.py:42](../service/app/logic/file_handler/uploader.py#L42) `upload_file` | 락 없이 호출 | ❌ |
| [uploader.py:98](../service/app/logic/file_handler/uploader.py#L98) `create_cache` | 락 없이 호출 | ❌ |
| [store.py:59](../service/app/logic/store.py#L59) `cache_set` | `replace_one(upsert=True)` | ✅ (덮어쓰기만 방지, 중복 LLM 호출은 못 막음) |
| [runner.py](../service/app/logic/runner.py) `llm_request_async` | done_check → miss → 실제 호출 | ❌ |

요약: **Redis 쓰기는 덮어쓰기 가능, MDB 쓰기는 idempotent, 비싼 호출(upload/create_cache/LLM)은 모두 중복 가능**.

---

## 3. 제안 — 인메모리 single-flight

### 3.1 핵심 아이디어

같은 key에 대한 작업이 **진행 중이면, 두 번째 이후 요청은 첫 번째의 결과를 공유**한다. 첫 번째가 실패하면 실패도 공유한다. 이를 파이썬 `dict[key, asyncio.Future]` 로 구현한다.

### 3.2 기본 구조

```python
# app/logic/file_handler/single_flight.py (신규)

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class InFlightRegistry:
    """같은 key에 대한 동시 작업을 1회로 coalesce한다.

    단일 프로세스(단일 replica) 안에서만 동작. 다중 replica 환경에서는
    Redis 기반 분산 락으로 교체 필요.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._in_flight: dict[str, asyncio.Future] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        """key에 대해 factory()를 호출하되, 이미 진행 중이면 기존 결과를 공유한다.

        - 리더(첫 호출): factory() 실행 → Future resolve
        - 팔로워: 리더의 Future를 await (같은 결과/예외 공유)
        - 완료 후 dict에서 제거 (다음 요청은 새로 시작)
        """
        if fut := self._in_flight.get(key):
            return await fut  # 팔로워 경로

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[T] = loop.create_future()
        self._in_flight[key] = fut
        try:
            result = await factory()
            fut.set_result(result)
            return result
        except BaseException as e:
            fut.set_exception(e)
            raise
        finally:
            self._in_flight.pop(key, None)


# 모듈 레벨 싱글톤 (전역 공유)
files_api_registry = InFlightRegistry("files_api")
context_cache_registry = InFlightRegistry("context_cache")
llm_call_registry = InFlightRegistry("llm_call")
```

### 3.3 동작 시나리오 (3개 동시 요청)

```
t=0ms   req A, B, C 모두 runner.llm_request_async() 진입
t=1ms   A가 llm_call_registry.run(cache_key, ...) 진입
          → _in_flight[cache_key] = future_A (생성)
          → factory() = 실제 upload + call 전체 실행 시작
t=2ms   B가 llm_call_registry.run(cache_key, ...) 진입
          → _in_flight[cache_key] 존재 → await future_A  (대기)
t=3ms   C도 동일 → await future_A  (대기)
t=15s   A의 factory() 완료, MDB 저장, future_A.set_result(response)
          → B, C의 await future_A 도 동시에 깨어남 → 같은 response 공유
          → _in_flight.pop(cache_key)
```

**결과**: `upload_file` 1회, `create_cache` 1회, `llm_handler.call` 1회, MDB 저장 1회. B/C는 A의 결과를 그대로 받음.

### 3.4 계층별 적용

| 계층 | key | registry | factory 내용 |
|------|-----|----------|--------------|
| Files API 업로드 | `file_checksum` | `files_api_registry` | `upload_file()` → `save_files_api()` |
| context_cache 생성 | `hash_key` (체크섬+모델+시스템) | `context_cache_registry` | `create_cache()` → `save_context_cache()` |
| LLM 호출 | `cache_key` (전체 입력 해시) | `llm_call_registry` | `done_check` → 파일 처리 → `call()` → `save_result()` |

→ 어느 계층에서 dedup 되어도 **하위 계층은 자동으로 호출 자체가 없어짐**. 예: LLM 레이어에서 dedup 되면 아예 파일 업로드/create_cache까지 가지 않는다.

### 3.5 runner.py 통합 스케치

```python
# runner.py (기존 함수 감싸기)

from app.logic.file_handler.single_flight import llm_call_registry

async def llm_request_async(llm_input, file_metas, ...):
    prepared = await prepare(llm_input, file_metas, ...)
    if prepared.cached_result is not None:
        return prepared.cached_result, prepared.cached_meta

    # ── single-flight 래핑 ──
    async def _do_work():
        call_input = await resolve_files(file_metas, llm_input, ...)
        llm_output = await aio_call(**call_input)
        return finalize(prepared, llm_output)

    return await llm_call_registry.run(prepared.cache_key, _do_work)
```

Files API/context_cache 레이어도 동일 패턴으로 resolver.py 내부에서 래핑한다.

---

## 4. 대안 비교

### 4.1 Approach 2 — Redis/MDB standby 레코드

```
SETNX files_api:{checksum}:lock "pending" EX 60  → 리더만 성공
팔로워: lock 감지 → 폴링 (100ms 간격) 또는 pub/sub 대기
리더: upload → SET files_api:{checksum} <uri> → DEL lock
```

| 장점 | 단점 |
|------|------|
| 다중 replica에서도 동작 | 단일 replica엔 오버킬 |
| Redis에 "뭐가 pending" 바로 보임 (observability) | 폴링/pub-sub 구현 복잡 |
| 리더 크래시 시 TTL로 자동 해제 | TTL 대기 시간 동안 팔로워 블로킹 |
| | 왕복 I/O 추가 (Redis 조회 ×N) |

**결론**: 현재는 과하다. 나중에 다중 replica로 전환할 때 전환.

### 4.2 Approach 3 — idempotent write만 (coalescing 포기)

```
Redis: SET NX → 첫 쓰기만 성공, 나머지는 무시
MDB: replace_one(upsert=True) 이미 사용 중
```

| 장점 | 단점 |
|------|------|
| 구현 거의 0 | Gemini upload N회, context_cache N개, LLM 호출 N회 그대로 발생 |
| | **비용 폭증 문제 해결 안 됨** |

**결론**: dedup 목적이 비용 절감이라면 이것만으론 부족. 단 **safety net**으로는 유효 (Approach 1과 병행).

### 4.3 file_manager가 Gemini 업로드 소유

파일 업로드와 Redis 캐싱을 llm-request가 아닌 file_manager 쪽으로 이관. file_manager가 `get_or_upload(checksum)` 을 제공하며 내부에서 dedup.

| 장점 | 단점 |
|------|------|
| 파일 관련 일관성이 file_manager로 집중 (Single Source of Truth) | 서비스 간 책임 이동 — 대공사 |
| file_manager가 여러 소비자(llm-request 외)를 가질 때 의미 있음 | llm-request는 여전히 LLM 호출 dedup 필요 → 결국 Approach 1 또 구현 |

**결론**: 파일 계층만 해결, LLM 계층은 여전히 Approach 1 필요. 장기적으로 고려할 수 있으나 **지금 범위 밖**.

---

## 5. 최종 권장안

> **Approach 1 (인메모리 single-flight) + Approach 3 (idempotent write safety net)**

- **비싼 호출 3개 계층**(Files API / context_cache / LLM)에 `InFlightRegistry` 도입
- 저장소 쓰기는 idempotent하게 유지 (Redis는 `SET NX EX` or 그대로 두어도 dedup 덕에 충돌 없음, MDB는 이미 `upsert=True`)

### 5.1 장점

- 단일 replica 가정에서 **거의 공짜로 완전 dedup 달성**
- 파이썬 dict + Future만 사용 → 외부 의존성 0, 레이턴시 오버헤드 0
- 리더 실패 시 팔로워도 같은 예외 전파 → 에러 핸들링 경로 1개로 통일
- 테스트 쉬움 (asyncio.gather 로 동시 호출 모의)

### 5.2 한계와 리스크

| 리스크 | 대응 |
|--------|------|
| 서버 재시작 시 in-flight 상태 손실 | 새 요청이 다시 리더가 되어 재업로드/재호출. 데이터 정합성 영향 없음. |
| 다중 replica 전환 시 무용지물 | `InFlightRegistry` 인터페이스만 유지하고 **내부만 Redis 분산 락으로 교체**. 호출부는 수정 불필요. |
| 리더가 영원히 끝나지 않으면 팔로워 영구 대기 | factory에 timeout 걸기 (`asyncio.wait_for`). |
| 메모리 누수 (dict 비대해짐) | `finally`에서 반드시 `pop` — 구현상 보장됨. |

### 5.3 다중 replica 전환 경로 (미래)

```python
class RedisInFlightRegistry(InFlightRegistry):
    """Approach 2를 구현체로 투입. 인터페이스 동일."""
    async def run(self, key, factory):
        # SETNX로 리더 선점
        # 리더: factory() → 결과를 Redis에 publish
        # 팔로워: pub/sub 구독 또는 폴링
        ...
```

호출부(`runner.py`, `resolver.py`)는 `llm_call_registry.run(...)` 그대로. **인터페이스가 미래 확장을 흡수**한다.

---

## 6. 적용 범위 (이번 작업 스코프)

- [ ] `app/logic/file_handler/single_flight.py` 신설 — `InFlightRegistry` + 3개 싱글톤
- [ ] `resolver.py`의 `_process_files_api`를 `files_api_registry.run(checksum, ...)`로 래핑
- [ ] `resolver.py`의 context_cache 생성 지점을 `context_cache_registry.run(hash_key, ...)`로 래핑
- [ ] `runner.py`의 LLM 호출 지점을 `llm_call_registry.run(cache_key, ...)`로 래핑
- [ ] `sdk/tests/` 또는 `service/tests/`에 동시성 테스트 추가
  - `asyncio.gather`로 동일 요청 3회 호출 → `upload_file` mock이 1회만 호출되는지 검증
  - 리더 실패 시 팔로워도 동일 예외 받는지 검증
  - 리더 성공 후 새 요청은 **새 리더가 되는지** 검증 (dict에서 pop 확인)

### 스코프 아웃 (별도 phase)
- 다중 replica 전환 (`RedisInFlightRegistry` 구현)
- file_manager로의 파일 책임 이관
- observability 대시보드 (현재 dedup 되는 요청 수 집계 등)

---

## 7. 사용 예시 (구현 후 기대 동작)

```python
# 에이전트 측 — 3개 동시 요청
results = await asyncio.gather(
    llm_request_async({"contents": "이 PDF 요약", ...}, file_metas=[pdf]),
    llm_request_async({"contents": "이 PDF 요약", ...}, file_metas=[pdf]),
    llm_request_async({"contents": "이 PDF 요약", ...}, file_metas=[pdf]),
)

# 서버 로그:
# [files_api_registry] leader=req_A checksum=abc123
# [files_api_registry] follower=req_B checksum=abc123 (waiting)
# [files_api_registry] follower=req_C checksum=abc123 (waiting)
# [upload_file] abc123 → uri=gs://... (1회만)
# [llm_call_registry] leader=req_A cache_key=def456
# [llm_call_registry] follower=req_B cache_key=def456 (waiting)
# [llm_handler.call] 1회 호출
# [save_result] MDB 1회 저장
# → req_A, req_B, req_C 모두 동일한 response 반환

# 비용:
# upload: 1회 (3회 → 1회)
# create_cache: 1회
# LLM: 1회
# MDB write: 1회
```

---

## 8. 논의 포인트 (상사 / 팀 검토용)

1. **단일 replica 가정이 타당한가?** 현재/가까운 미래에 다중 replica 계획이 있다면 처음부터 Approach 2로 가는 것도 선택지.
2. **적용 계층 범위**: 3개 계층 모두 할지, 가장 비싼 LLM 호출만 우선 할지 (LLM만 해도 하위 호출은 자동 중복 방지됨).
3. **타임아웃 정책**: 리더가 끝나지 않을 때 팔로워를 얼마나 기다릴지. LLM 호출 기본 타임아웃과 정렬.
4. **모니터링**: dedup이 실제로 얼마나 일어나는지 지표화 필요한가.
5. **Phase A 완료 정의에 포함시킬지**, 별도 phase로 분리할지.
