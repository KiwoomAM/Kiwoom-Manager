# Dedup

같은 입력의 작업이 동시에 여러 요청에 의해 중복 실행되지 않도록 막는다 (cache stampede 방지).

## 무슨 문제를 막는가

캐시가 비어있는 상태에서 같은 입력으로 동시에 N개 요청이 들어오면, 각 요청이 모두 같은 작업(파일 업로드, LLM 호출 등)을 **별도로 수행**한다. 이를 **cache stampede**라고 한다.

예를 들어 같은 PDF를 첨부한 요청 10개가 동시에 들어오면, 첫 요청이 끝나기 전이라 캐시가 아직 비어있어서 10개 모두 Files API에 업로드하고 10번 LLM을 호출하게 된다.

llm-manager는 Redis `SETNX` 선점 방식으로 첫 요청만 작업을 수행하고, 나머지는 결과를 기다렸다가 재사용하도록 한다.

## 적용 대상

| 적용 대상 | 언제 | 효과 |
|---|---|---|
| Files API 업로드 | 같은 파일을 동시에 여러 요청이 보낼 때 | 한 요청만 업로드, 나머지는 결과 URI 재사용 |
| Context Cache 생성 | 같은 (파일 + 모델 + system + tools) 조합을 동시에 여러 요청이 시도할 때 | 한 요청만 생성, 나머지는 캐시된 cache_name 재사용 |
| LLM 응답 (`agent_llm`) | 같은 cache_key를 동시에 여러 요청이 LLM 호출하려 할 때 (**`save_mode=True` 인 경우만**) | 한 요청만 LLM 호출, 나머지는 MDB 결과 재사용 |

> inline 경로(작은 파일을 inline 전송)는 Redis를 거치지 않아 dedup 대상이 아니다.

> `agent_llm` dedup 이 `save_mode=True` 로 한정되는 이유: 대기자가 받아가는 것이 **리더의 MDB
> 저장분**이라, 저장하지 않는 조합에서 줄을 세우면 대기자가 빈손으로 깨어나 결국 자기가 LLM 을
> 다시 부른다. LLM 호출 수는 줄지 않고 지연만 쌓인다 (`docs/35`).

## 동작 방식

각 요청은 캐시 조회 → standby 키 SETNX → 작업 수행 (또는 대기) 순서로 진행된다.

```
요청 도착
  │
  ▼
① 본 캐시 조회
   ├─ HIT  → 결과 반환 (끝)
   └─ MISS ↓
② standby 키 SETNX 시도 (원자적)
   ├─ 성공 (자기가 선점) → ③ 자기가 작업 수행
   └─ 실패 (다른 요청이 작업 중) → ④ 대기
   │
   ▼
③ 자기가 작업 수행
   try:
       비싼 작업 (upload / create_cache / LLM 호출)
       본 캐시 SET
   finally:
       standby DEL
   → 결과 반환 (끝)
   │
   ▼
④ 대기 (exponential backoff)
   → 대기 중 본 캐시 재조회
      ├─ HIT → 결과 반환 (끝)
      └─ standby_ttl_seconds 초과 → 자기가 작업 시작 (③으로, 경고 로그)
```

### 대기 폴링 — Exponential Backoff

선점 실패한 요청은 다음 시퀀스로 본 캐시를 재조회한다.

```
시도 1:  0.5초 대기 → 본 캐시 재조회
시도 2:  1초 대기   → 재조회
시도 3:  2초 대기   → 재조회
시도 4:  4초 대기   → 재조회
시도 5:  8초 대기   → 재조회
시도 6:  10초 대기 (cap) → 재조회
시도 7~: 10초 간격 반복
```

| 파라미터 | 값 |
|---|---|
| 초기 지연 | 0.5초 |
| 지연 배수 | ×2 (0.5, 1, 2, 4, 8) |
| 한 번 대기 cap | 10초 |
| 전체 timeout | `standby_ttl_seconds` (디폴트 600초) |

## 사용자 입력 — `standby_ttl_seconds`

`llm_request_async()`의 키워드 인자로 dedup 대기 timeout을 지정한다.

```python
response, meta = await llm_request_async(
    {"contents": "...", "model": "gemini-2.5-flash"},
    files=files,
    standby_ttl_seconds=600,   # 기본값. 동일 작업 중인 다른 요청을 최대 600초까지 대기
)
```

디폴트 600초(10분). 이 시간 동안 다른 요청의 작업 완료를 기다리며 본 캐시를 재조회한다.
초과 시 대기 포기 후 자기가 직접 작업 시작 (중복 발생 가능, [§4 한계](#한계) 참고).

## 한계

### Timeout 초과

`standby_ttl_seconds` 시간 안에 첫 요청이 완료되지 않으면, 대기 중이던 후속 요청은 자기가 직접 작업을 시작한다.

- **결과**: 두 요청이 같은 작업을 동시에 수행 (중복 발생)
- **LLM 비결정성**: 두 호출자가 LLM에서 다른 응답을 받을 수 있다
- **MDB 저장**: 마지막에 저장된 응답이 남는다 (upsert 덮어쓰기 — [caching.md §2.2](caching.md#cache_mode--save_mode) 참고)
- **로그**: `WARNING` 레벨로 timeout 발생 + standby 키 기록 — 빈도가 잦다면 `standby_ttl_seconds`를 늘려야 한다는 신호

### Redis 장애

Redis 연결 실패 시 dedup이 동작하지 않고 `CacheError`를 던진다. 호출자가 재시도 정책으로 처리한다.

### 프로세스 강제 종료

작업 중인 프로세스가 `SIGKILL`(`kill -9`)로 강제 종료되면 `finally` 블록이 실행되지 않아 standby 키가 남는다. 이 경우 `standby_ttl_seconds` 만료 후 자연 정리된다 — 그 사이 후속 요청들은 만료까지 대기 후 자기가 작업을 시작한다.
