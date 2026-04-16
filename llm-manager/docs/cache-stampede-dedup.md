# Cache Stampede Dedup 설계 — Redis standby 레코드 방식

> 동시 요청 N개가 같은 캐시 miss → 비싼 작업 중복 실행 방지.
> Redis에 "처리 중" standby 레코드를 저장하여 후속 요청은 대기.
> 적용 대상: Files API / context_cache / agent_llm(LLM 응답) 3계층.

## 1. 배경 / 문제 정의

llm-request는 비동기 서비스. 에이전트가 동일 요청을 동시 N건 보낼 수 있음 (예: `asyncio.gather`).
이 때 **cache stampede** 발생:

### 시나리오

```
t=0ms   req A, B, C 동시 도착
t=1ms   각자 done_check → MDB 비어있음 → 모두 cache miss
t=2ms   각자 Redis files_api:{checksum} 조회 → 모두 miss
t=3ms   각자 upload_file() → Gemini 업로드 3회  ⚠️
t=10s   각자 create_cache() → context_cache 3개 생성  ⚠️
t=11s   각자 llm_handler.call() → LLM 호출 3회  ⚠️
t=15s   각자 MDB save → 3회 덮어쓰기 (마지막이 이김)
```

### 계층별 피해

| 계층 | 중복 발생 | 피해 |
|---|---|---|
| Files API 업로드 | N회 | Gemini 업로드 비용 N배, Redis URI 마지막 것만 남음 |
| context_cache 생성 | N회 | 48시간 TTL 동안 N개 중복 저장 — 가장 큰 낭비 |
| LLM 호출 | N회 | 토큰 비용 N배 — 체감 비용 최대 |
| MDB save | N회 | 덮어쓰기만 발생 (`replace_one` upsert=True — _id 충돌 없음) |

### 원인

캐시 조회와 캐시 저장 사이의 **시간 간극**에 들어온 동시 요청들이 모두 "비어있음"을 보고 각자 비싼 작업을 시작하기 때문. 현재 코드는 이 간극을 막을 장치가 없음.

### 현재 코드 dedup 상태

| 위치 | 현재 동작 | dedup 여부 |
|---|---|---|
| `cache_store.py` `save_files_api` | redis SET (NX 아님) | ❌ |
| `cache_store.py` `save_context_cache` | redis SET (NX 아님) | ❌ |
| `uploader.py` `upload_file` | 락 없이 호출 | ❌ |
| `uploader.py` `create_cache` | 락 없이 호출 | ❌ |
| `store.py` `cache_set` | `replace_one(upsert=True)` | ✅ (덮어쓰기만 방지) |
| `runner.py` `llm_request_async` | done_check → miss → 실제 호출 | ❌ |

---

## 2. 해결 방향 — Redis standby 레코드

### 핵심 아이디어

- 작업 시작 직전: Redis에 `SETNX` 로 standby 키 등록 ("이 key 처리 중")
- 후속 요청: standby 감지 시 exponential backoff 대기 → 본 캐시 완료 시 결과 사용
- 작업 완료 시: 본 캐시 저장 + standby 삭제

### standby 키 구조 — 별도 prefix 블록

기존 본 캐시 키는 그대로 유지. standby 전용 키를 별도 네임스페이스로 추가.

```
기존 본 캐시 (변경 없음):
  file_cache:files_api:{checksum}        → {uris, file_size, ...}
  file_cache:context_cache:{hash_key}    → {cached_content_name, ...}
  MDB agent_llm._id = {cache_key}       → {result, metadata, ...}

신규 standby (Redis, 3개 추가):
  file_cache:files_api_standby:{checksum}        → {"started_at": "<ISO>"}
  file_cache:context_cache_standby:{hash_key}    → {"started_at": "<ISO>"}
  agent_llm_standby:{cache_key}                  → {"started_at": "<ISO>"}
```

**standby 값**: `{"started_at": "<ISO>"}` (로깅·디버깅용 최소 정보)
**standby 키 hash**: 본 캐시 키와 동일 해시 재사용 (checksum / hash_key / cache_key)
**standby TTL**: `standby_ttl_seconds` (디폴트 600초 = 10분, 호출자가 조정 가능)

### 적용 대상

- ✅ files_api (Files API 업로드 dedup)
- ✅ context_cache (context cache 생성 dedup)
- ✅ agent_llm (LLM 호출 dedup)
- ❌ inline 경로 (Redis 우회, 캐싱 안 함)

---

## 3. standby 생명주기

```
요청 도착:
  │
  ▼
① 본 캐시 조회
  ├─ HIT → 결과 반환 (끝)
  └─ MISS → ②로
  │
  ▼
② standby 키 SETNX 시도 (원자적)
  ├─ 성공 (자기가 선점) → ③으로
  └─ 실패 (다른 요청이 작업 중) → ④로
  │
  ▼
③ 자기가 작업 수행
  try:
      비싼 작업 (upload / create_cache / LLM 호출)
      본 캐시 SET
  finally:
      standby DEL
  │
  ▼
④ 대기 (exponential backoff)
  → 대기 중 본 캐시 재조회
     ├─ HIT → 결과 반환 (끝)
     └─ standby 만료 (10분) → 자기가 작업 시작 (③으로, 경고 로그)
```

---

## 4. 대기 방식 — Exponential Backoff 폴링

### 파라미터

| 파라미터 | 값 |
|---|---|
| 초기 지연 | 0.5초 |
| 지연 배수 | ×2 (0.5, 1, 2, 4, 8) |
| 한 번 대기 cap | 10초 |
| 전체 timeout | `standby_ttl_seconds` (디폴트 600초) |

### 시퀀스 (디폴트)

```
시도 1:  0.5초 대기 → 본 캐시 재조회
시도 2:  1초 대기   → 재조회
시도 3:  2초 대기   → 재조회
시도 4:  4초 대기   → 재조회
시도 5:  8초 대기   → 재조회
시도 6:  10초 대기 (cap) → 재조회
시도 7~: 10초 간격 반복
...
600초 경과 → timeout
```

### timeout 도달 시 (Y' Hybrid)

- **자기가 작업 시작** + 경고 로그
- 선행 작업이 살아있다면 두 작업 동시 → 본 캐시 덮어쓰기 (결과 동일)
- LLM 비결정성으로 두 호출자가 다른 응답 받을 수 있음 — README에 한계 안내

```python
logger.warning(
    "standby 대기 timeout(%d초) 초과 — 자기가 작업 시작. "
    "standby_key=%s, 중복 발생 가능. 빈도 높으면 standby_ttl_seconds 늘리세요.",
    standby_ttl_seconds, standby_key,
)
```

---

## 5. 입력 인자

### `standby_ttl_seconds` (호출자 input)

`llm_request_async` / `llm_request_stream_async`의 인자로 추가:

```python
async def llm_request_async(
    llm_input: dict,
    *,
    file_metas: list[FileMeta] | None = None,
    standby_ttl_seconds: int = 600,   # 신규 — 기본 10분
    cache_mode: bool = True,
    ...
):
```

호출자 가이드:
- **기본 600초 (10분)** — 일반 LLM + Files API 업로드 커버
- **짧은 작업**: 120~300초로 줄임 (죽음 빨리 감지)
- **대용량 + 무거운 LLM**: 900~1200초로 늘림 (false timeout 방지)

### 관련 인자 정리

| 인자 | 용도 | 기본값 |
|---|---|---|
| `standby_ttl_seconds` | standby 키 TTL + 후속 대기 timeout | 600 |
| `ttl_seconds` (기존) | MDB 캐시 만료 시간 | None |
| `context_cache_ttl` (기존) | Gemini context cache TTL | None |

---

## 6. 모듈 구조

### 신규: `service/app/logic/file_handler/dedup.py`

3계층 공통 standby 패턴. prefix + key 만 다르고 로직 동일.

```python
async def try_acquire_standby(prefix: str, key: str, ttl_seconds: int = 600) -> bool:
    """standby SETNX 시도. True면 자기가 작업. False면 이미 작업 중.

    Redis: SET file_cache:{prefix}_standby:{key} = {"started_at": "..."} NX EX ttl
    """

async def wait_for_standby_release(
    prefix: str,
    key: str,
    timeout: int = 600,
    find_cache_fn: Callable[[], dict | None] | None = None,
) -> dict | None:
    """standby 사라질 때까지 exponential backoff 대기.

    대기 중 본 캐시 재조회 (find_cache_fn 제공 시):
      - HIT → 결과 dict 반환 (즉시)
      - MISS + standby 사라짐 → None 반환 (호출자가 작업 시작)
      - timeout → None 반환 + 경고 로그 (Y' Hybrid)
    """

def release_standby(prefix: str, key: str) -> None:
    """standby DEL. finally 블록에서 호출."""
```

### 호출 위치

**files_api** — `resolver.py` `_process_files_api` (각 파일별 루프 안):
```python
existing = cache_store.find_files_api(checksum)
if existing:
    ...  # 분기 B
else:
    acquired = await dedup.try_acquire_standby("files_api", checksum, standby_ttl)
    if acquired:
        try:
            ...  # 분기 C (upload)
            cache_store.save_files_api(checksum, doc)
        finally:
            dedup.release_standby("files_api", checksum)
    else:
        cached = await dedup.wait_for_standby_release(
            "files_api", checksum, standby_ttl,
            find_cache_fn=lambda: cache_store.find_files_api(checksum),
        )
        if cached:
            ...  # 분기 B (기존 URI 재사용)
        else:
            ...  # timeout — 자기가 upload (Y')
```

**context_cache** — `resolver.py` `_process_files_api` (create_context_cache 호출 전):
```python
# context_cache hash_key에 대한 standby
acquired = await dedup.try_acquire_standby("context_cache", hash_key, standby_ttl)
...
```

**agent_llm** — `runner.py` (prepare 이후, resolve_files/aio_call 전):
```python
# MDB cache_key에 대한 standby
acquired = await dedup.try_acquire_standby("agent_llm", prep.dc.cache_key, standby_ttl)
if acquired:
    try:
        call_input = await _build_server_call_input(prep)
        res = await aio_call(**call_input)
        finalize(prep, res, ...)
    finally:
        dedup.release_standby("agent_llm", prep.dc.cache_key)
else:
    cached = await dedup.wait_for_standby_release(
        "agent_llm", prep.dc.cache_key, standby_ttl,
        find_cache_fn=lambda: _try_done_check(prep, database),
    )
    if cached:
        ...  # 본 캐시 hit 결과 반환
    else:
        ...  # timeout — 자기가 LLM 호출 (Y')
```

---

## 7. 에러 처리

### 예외 발생 시 standby 정리

- `finally` 블록에서 `release_standby` 호출
- Python `finally`는 거의 모든 예외·asyncio 취소·인터럽트에서 실행됨
- 프로세스 강제 종료 (kill -9) 만 불가 → **standby TTL 만료로 자연 정리** (10분)

### standby SETNX 실패 시 (Redis 장애)

- 연결 오류라면 `CacheError` raise (기존 에러 정책 따름)
- handler 재시도 위임 원칙 유지

### timeout 시 (Y' Hybrid)

- 경고 로그 + 자기가 작업 시작
- 본 캐시 덮어쓰기 허용 (SETNX 안 씀)
- README에 동시 호출 한계 안내

---

## 8. 동시성 한계 — README 안내 예정

```markdown
### 동시 호출 한계 (cache stampede dedup)

같은 input의 동시 요청은 standby 메커니즘으로 dedup 시도하지만:

- 첫 요청이 `standby_ttl_seconds`(기본 10분) 내에 완료되면
  후속 요청은 자동으로 캐시 결과를 사용합니다.
- 첫 요청이 `standby_ttl_seconds` 내에 완료되지 않으면,
  후속 요청이 자기 작업을 시작합니다.
  - LLM 비결정성으로 두 요청이 다른 응답을 받을 수 있습니다.
  - 캐시(MDB)는 마지막 저장된 응답으로 유지됩니다.
- `standby_ttl_seconds`를 조정하여 작업 소요 시간에 맞게 설정하세요.
```

---

## 9. TODO

### Phase 1 — 기본 구현

- [ ] `service/app/logic/file_handler/dedup.py` 신설
- [ ] `try_acquire_standby(prefix, key, ttl_seconds)` 구현 (Redis SETNX + EX)
- [ ] `wait_for_standby_release(prefix, key, timeout, find_cache_fn)` 구현 (exponential backoff)
- [ ] `release_standby(prefix, key)` 구현 (DEL)
- [ ] `resolver.py` `_process_files_api`에 files_api standby 적용
- [ ] `resolver.py` `_process_files_api`에 context_cache standby 적용
- [ ] `runner.py` `llm_request_async` / `llm_request_stream_async`에 agent_llm standby 적용
- [ ] `llm_request_async` / `llm_request_stream_async` 인자에 `standby_ttl_seconds: int = 600` 추가
- [ ] SDK `_client.py` body에 `standby_ttl_seconds` 전달
- [ ] SDK `__init__.py` public API에 `standby_ttl_seconds` 인자 추가
- [ ] DTO `LLMRequestBody`에 `standby_ttl_seconds: int = 600` 필드 추가
- [ ] 라우터 `handle_request` / `handle_request_stream`에서 `standby_ttl_seconds` 전달
- [ ] `configure_handlers()`에 Redis startup health check 추가 (`init_redis()` False → `RuntimeError` raise)
- [ ] `configure_handlers()`에 startup standby flush 추가 (`get_client().keys(pattern)` → `delete`)
- [ ] `docs/single-flight-dedup.md` 삭제 (기존 초안 폐기)
- [ ] README에 동시 호출 한계 섹션 추가
- [ ] 테스트: standby 선점/대기/timeout 각 케이스

### Phase 2 — 모니터링·정교화 (후속)

- [ ] standby timeout 빈도 로깅 → 모니터링 대시보드
- [ ] standby TTL 자동 조정 (이전 작업 소요 시간 기반)
- [ ] 본 캐시 SETNX 옵션 추가 (캐시 일관성 강화)
- [ ] **sync/async 경계 재검토** — dedup 구현 완료 후 전체 sync/async 정의 재확인. 현재 혼합 상태:
  - Redis (redis-handler/redis-py) = sync
  - MDB (mdb-handler/motor), LLM (llm-handler) = async
  - dedup.py는 async로 통일하고 내부에서 sync/async 혼합 처리 (`inspect.iscoroutine` 분기)
  - 구현 끝나면 일관성/가독성 관점에서 재검토 필요

---

## 10. Redis 의존성

- standby SETNX는 **Redis 전용** — 메모리 폴백 없음
- Redis 미연결 시 dedup 불가능 → llm-request 기동 자체를 막음
- `configure_handlers()`에서 startup health check: `init_redis()` 반환값 확인 → `False`면 `RuntimeError` raise

```python
# providers.py configure_handlers() 내
if not init_redis():
    raise RuntimeError("Redis 연결 실패 — llm-request를 시작할 수 없습니다")
```

> **주의**: `init_redis()`는 연결 실패 시 예외를 올리지 않고 `False`를 반환한다. 명시적 반환값 체크 필요.

---

## 11. SETNX 구현 방법

`redis-handler`에 `setnx()` 미구현 → Issue raise 예정 (`redis-handler-setnx-request.md`)

Issue 해결 전까지: `dedup.py`에서 `redis-handler` 내부 client 직접 사용

```python
# dedup.py — redis-handler setnx 구현 전 임시 방편
from redis_handler.connection import get_client

client = get_client()
result = client.set(key, value, nx=True, ex=ttl_seconds)
acquired = result is not None  # True면 점유 성공, None이면 이미 존재
```

---

## 12. 서버 아키텍처 및 Startup Standby Flush

### 아키텍처 확인

- llm-request = 단일 Docker = 단일 FastAPI 프로세스
- 동시 요청 = 같은 asyncio 이벤트 루프 내 코루틴 (멀티프로세스 아님)
- 따라서 Redis SETNX가 올바른 dedup 메커니즘 (OS-level mutex 불필요)

### Startup Standby Flush

서버 재기동 시 이전 세션의 stale standby 키 존재 가능 (SIGKILL 등으로 `finally` 미실행)  
단일 docker → 다른 서버 인스턴스 없음 → **전체 flush 안전**

```python
# providers.py configure_handlers() 내
# redis-handler에 delete_pattern 없음 → get_client()로 raw 클라이언트 직접 사용
from redis_handler import get_client
client = get_client()
if client:
    for pattern in ("file_cache:*_standby:*", "agent_llm_standby:*"):
        keys = client.keys(pattern)
        if keys:
            client.delete(*keys)
```

> **주의**: 멀티 인스턴스 환경(K8s 등)으로 확장 시 전체 flush 대신 인스턴스 ID 기반 selective flush로 전환 필요.
