# Observability

`llm-manager`가 요청을 어떻게 처리했는지 두 가지 채널로 관찰할 수 있다.

| 채널 | 누가 보나 | 무엇이 보이나 |
|---|---|---|
| `meta` (반환값) | **클라이언트** — `llm_request_async()` 응답의 두 번째 튜플 원소 | 캐시 hit/miss, cache_key, 라우팅 결정 등 응답 메타데이터 |
| 서비스 로그 | **운영자** — Service의 stdout (uvicorn) | 파일 처리 경로, dedup 선점 역할, 단계별 오류 |

> **방침**: `meta` 응답 스키마는 프로덕션 고정. 새 관찰 정보는 서버 로그(`logger.info`/`logger.debug`)로만 추가된다. 클라이언트 SDK 호출 코드 변경은 없다.

## 클라이언트가 보는 정보 — `meta`

`llm_request_async()` 응답의 `meta`(= `ResponseMeta`)를 통해 처리 결과를 확인한다.

### 핵심 필드

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | `str` | `'live'` (LLM 직접 호출) 또는 `'cache'` (캐시 hit) |
| `cache_hit` | `bool` | 캐시 히트 여부 (`source == 'cache'`와 동일) |
| `cache_key` | `str \| None` | SHA-256 캐시 키 (64자 hex) |
| `metadata` | `dict` | `{"service_name": ..., "model": ...}` |
| `collection` | `str \| None` | MongoDB 컬렉션명 |
| `file_meta` | `list[dict] \| None` | files 호출 시 file-manager 부여 메타 |
| `route_reason` | `str \| None` | `debug=True`일 때만 — 라우팅 결정 이유 |

### `route_reason` (debug=True)

호출 시 `debug=True`를 주면 `route_reason`에 분기 이유가 박힌다.

| 값 | 의미 |
|---|---|
| `cache_miss` | 캐시 조회했지만 없어서 LLM 호출 |
| `cache_hit` | 캐시 hit으로 LLM 호출 안 함 |
| `cache_off_save_off` | `cache_mode=False, save_mode=False` |
| `cache_off_save_on` | `cache_mode=False, save_mode=True` (강제 갱신 + 저장) |
| `cache_error_fallback` | 캐시 조회 자체에 오류 — LLM 호출로 fallback |
| `cache_deserialize_error` | 캐시 doc 역직렬화 실패 — LLM 호출로 fallback |
| `standby_wait_hit` | dedup 대기 중 본 캐시 HIT 발견 |

### 사용 예시

```python
response, meta = await llm_request_async(llm_input, debug=True)

if meta.cache_hit:
    print(f"cached: {meta.cache_key[:16]}...")
else:
    print(f"live: route_reason={meta.route_reason}")
```

`file_meta`(파일 첨부 호출 시) 활용은 [api-reference.md](../api-reference.md#requestmeta) 참고.

## 서비스 로그 패턴

서비스 stdout에 두 가지 prefix 패턴이 박혀있다 — `grep`으로 빠르게 추출 가능.

**통일 원칙**: 처리 경로가 신규인지 재사용인지 한눈에 파악되도록 `source=` 키를 공통으로 쓴다.

| `source` | 의미 |
|---|---|
| `live` | 신규 처리 (Files API 업로드, Context Cache 신규 생성 등) |
| `cached` | Redis 재사용 |
| `none` | 조건 미충족 (예: Context Cache 켰지만 Files API 경로 파일이 없음) |

### `[route]` — 파일별 처리 경로

각 파일이 inline / Files API / Context Cache 중 어느 길로 갔는지 기록 (`resolver.py`).

```
[route] file=image.png mode=inline
[route] file=medium.pdf mode=files_api source=cached uris=1
[route] file=large.pdf mode=files_api source=live parts=2 duration_ms=12000
[route] mode=context_cache source=cached name=cachedContents/abc
[route] mode=context_cache source=live name=cachedContents/abc
[route] mode=context_cache source=none reason=no_files_api_files
```

| 키 | 값 |
|---|---|
| `mode` | `inline` / `files_api` / `context_cache` |
| `source` | `live` / `cached` / `none` (위 통일 원칙) |
| `uris` | (Files API) 재사용된 URI 개수 |
| `parts` | (Files API, PDF 분할) 업로드된 파트 수 |
| `duration_ms` | (live) 신규 업로드 / 캐시 생성 소요 ms |
| `name` | (Context Cache) Gemini가 부여한 cache name |
| `reason` | (`source=none`) 조건 미충족 이유 |

### `[dedup]` — 선점 역할

같은 입력의 동시 요청에서 자기가 어떤 역할을 맡았는지 기록 (`runner.py`).

```
[dedup] role=leader key=abc12345
[dedup] role=waiter wait_ms=2134 leader_completed=true
[dedup] role=standby_timeout wait_ms=10000
```

| 값 | 의미 |
|---|---|
| `role=leader` | 본인이 SETNX 선점 → 작업 수행 |
| `role=waiter` | 다른 요청이 선점 → 대기 후 본 캐시 HIT (`leader_completed=true`) |
| `role=standby_timeout` | `standby_ttl_seconds` 초과 → 자기가 작업 시작 (중복 발생, [dedup.md §4](dedup.md#한계)) |

### `grep` 예시

```bash
# 특정 파일이 어떤 경로로 갔나
grep "file=report.pdf" service.log

# Files API 신규 업로드만 (cached 제외)
grep "[route].*mode=files_api source=live" service.log

# dedup standby timeout 발생 빈도 — 잦으면 standby_ttl_seconds 조정 신호
grep "[dedup] role=standby_timeout" service.log | wc -l
```

## 디버깅 시 활용

위 `meta` + 로그 패턴으로 일반적인 흐름은 파악할 수 있다. 더 깊은 디버깅은 [debugging-guide.md](../../debugging-guide.md)를 참고:

- §2 단계별 상세 — 각 처리 단계의 정상/경고/오류 로그 패턴 (`🟢` / `🟡` / `🔴`)
- §3 예외 클래스 레퍼런스 — Service / SDK 양쪽 예외 정의
- §4 에러 시나리오 FAQ — 흔한 증상별 원인·대응

`request_id`(에러 응답에 포함)로 클라이언트가 받은 에러를 서버 로그와 매칭할 수 있다 ([error-handling.md §3](error-handling.md#request_id로-서버-로그-매칭)).
