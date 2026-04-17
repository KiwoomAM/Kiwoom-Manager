# Observability Backlog

개발 초기 단계에서 llm-request가 요청 하나를 처리할 때 내부에서 어떤 분기를 탔는지,
캐시 상태와 동시성 역할을 **서비스 로그**(uvicorn stdout)만 보고 파악할 수 있도록 하기 위한 로그 추가 목록.

**방침**
- `meta` 응답 스키마는 건드리지 않는다 (프로덕션 스키마 고정).
- SDK/클라이언트 호출 코드 변경 없음.
- 서버 코드에 `logger.info` / `logger.debug` 만 추가.
- 로그 형식은 `key=value` 쌍 위주로 구성해 grep/필터링이 쉽도록.

---

## 현재 상태 — meta로 이미 관찰 가능한 것

| 정보 | 경로 |
|------|------|
| LLM 응답 캐시(MDB) hit/miss | `meta.source`, `meta.cache_hit`, `meta.cache_key` |
| MDB 라우팅 이유 (debug=True) | `meta.route_reason`: `cache_miss`, `cache_hit`, `cache_off_save_off`, `cache_off_save_on`, `cache_error_fallback`, `cache_deserialize_error`, `standby_wait_hit` |

즉 **LLM 응답 관점**은 보이는데, **파일 처리·Context Cache·dedup 상세 역할**은 meta에도 로그에도 없다.

---

## ✅ 구현됨 (2026-04-17)

아래 4개 항목 모두 [resolver.py](../service/app/logic/file_handler/resolver.py) / [runner.py](../service/app/logic/runner.py)에 반영.

**통일 원칙**: `source=live` (신규 처리) / `source=cached` (Redis 재사용) / `source=none` (조건 미충족)

### P1. 파일별 경로 결정 로그

**로그 위치**: resolver.py — `_process_inline`, `_reuse_files_api_uris`, `_upload_and_cache_file`

```
[route] file=image.png mode=inline
[route] file=medium.pdf mode=files_api source=cached uris=1
[route] file=large.pdf mode=files_api source=live parts=2 duration_ms=12000
```

### P1. Files API URI Redis 캐시 hit/miss

위 `[route]` 로그에 통합 — `source=cached` (URI 재사용) / `source=live` (신규 업로드 + duration_ms)

### P2. Context Cache 상태

**로그 위치**: resolver.py — `_process_files_api` 분기 A/④, `_create_and_save_context_cache`

```
[route] mode=context_cache source=cached name=cachedContents/abc   ← Redis hit
[route] mode=context_cache source=live name=cachedContents/abc     ← 신규 생성
[route] mode=context_cache source=none reason=no_files_api_files   ← 조건 미충족
```

### P2. Dedup 역할 상세

**로그 위치**: runner.py — `llm_request_async`, `llm_request_stream_async` standby 분기

```
[dedup] role=leader key=abc12345
[dedup] role=waiter wait_ms=2134 leader_completed=true
[dedup] role=standby_timeout wait_ms=10000
```

---

## ✅ 해결됨

### `create_cache` 인자명 불일치 (2026-04-17 수정)
- 증상: `create_cache() got an unexpected keyword argument 'system_instruction'` (502)
- 원인: llm-handler PR v0.1.1 Breaking Change 미반영 (handler는 `system=`, llm-request는 `system_instruction=`으로 호출)
- 수정: [uploader.py:85-103](../service/app/logic/file_handler/uploader.py#L85) `system=system_instruction`로 kwarg 매핑, contents를 `list[FileRef]` 직접 전달로 변경
- 함께: [resolver.py:185](../service/app/logic/file_handler/resolver.py#L185) `upload_file(path, mime_type=...)` kwarg화 (positional 2번째가 display_name으로 바뀐 것 대응)

### SDK timings 통합 (2026-04-17 수정)
- 증상: `_configure(timeout=...)` private API로만 HTTP timeout 설정 가능. ttl 3종이 공개 함수 시그니처에 흩어져 있음
- 수정: `Timings` TypedDict 도입 → 4개 공개 함수에 `timings: Timings | None = None` 단일 인자로 통합. 기본 `http_timeout=600` 으로 상향. `_configure` 삭제
- 키: `http_timeout`, `mdb_ttl`, `context_cache_ttl`, `standby_ttl` (선택적, 미지정 시 기본값)

---
