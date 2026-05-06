# Pull Request — llm-request Phase 3: Method 1 폐기 + Lazy Fetch

> **Spec:** [docs/21.method1-deprecation-lazy-fetch.md](./21.method1-deprecation-lazy-fetch.md)
> **Plan:** [docs/22.phase3-plan.md](./22.phase3-plan.md)
> **Pre-requisite:** PR 1 (file-manager: `register_or_get` 헬퍼) merged.
> **참고 스타일:** [pull_request_v2.md](./pull_request_v2.md)

# 📝 개요

Method 1 (data-mode `files=[FileInput(bytes)]`) 폐기. agent 가 file-manager 에 직접 등록 (`register_or_get`) 후 받은 `file_path + 5 메타` 만 llm-request 에 전달. llm-request 는 **lazy fetch** — 4·5 단계 standby leader 만 file-manager 에서 bytes 를 가져온다.

핵심 효과:
- **agent 메모리**: 같은 파일 N 회 호출에도 bytes 1 회만 적재 (이후 release)
- **llm-request 메모리 (files_api 경로)**: leader 1 명만 업로드 + URI 를 Redis 에 캐시 → waiter 재사용. 동시 N 요청에서도 bytes 1 회만 메모리 적재
- **llm-request 메모리 (inline 경로)**: standby 보호 없음 — 결과 (bytes) 가 Redis 에 캐시 안 되므로 dedup 무효익. 동시 N 요청 시 N 회 fetch (의도된 trade-off, 작은 파일 한정)
- **네트워크**: agent → llm-request 의 bytes 전송 0 (file_path 만)

# 🔗 관련 이슈

#527

# ✅ 변경 사항

## 핵심 흐름

```
Agent: file_manager.register_or_get(bytes, meta) → 6 필드 dict → bytes release
       ↓
       llm_request_async(stored_files=[StoredFile(**dict)])
       ↓
llm-request:
  ① file_resolver.resolve_stored_files
       — placeholder FileInput(data=b"") 만 만들고 validators 호출 (mime/size 만 검증)
  ② resolver.classify_files / resolve_files
       — 분류만 진행, bytes 는 아직 fetch 안 함
  ③ lazy fetch (`_process_inline` / `_upload_and_cache_file`)
       — `if not fi.data: fi.data = await fetch_with_fallback(fm, fi)`
       — files_api 경로: try_acquire_standby 가 leader 1 명만 진입시켜 dedup
       — inline 경로: standby 없음, 동시 N 요청 시 N 회 fetch (의도)
  ④ LLM 호출 + finalize
```

## 변경 위치

| # | 파일 | 변경 |
|---|------|-----|
| 1 | `service/app/models/requests.py` | `LLMRequestBody.files` 제거 + `model_config = ConfigDict(extra="forbid")` / `FileInput.data` default `b""` |
| 2 | `service/app/logic/file_register.py` | **삭제** (Method 1 폐기) |
| 3 | `service/tests/logic/test_file_register.py` | **삭제** |
| 4 | `service/app/logic/file_resolver.py` | `resolve_stored_files` lazy 화 (placeholder FileInput + validators 만) / `fetch_with_fallback(fm, fi)` 헬퍼 추출 (404 → check_cache → 새 path 으로 재시도) |
| 5 | `service/app/logic/file_processing/resolver.py` | `_process_inline` / `_upload_and_cache_file` leader 분기 진입 직후 `if not fi.data: fi.data = await fetch_with_fallback(fm, fi)` |
| 6 | `service/app/logic/runner.py` | 4 진입점에서 `files` 인자 제거 / `_prepare_files(stored_files)` 단일 인자 단순화 |
| 7 | `service/app/routers/request.py` | 2 핸들러에서 `files=body.files` 제거 |
| 8 | `sdk/llm_request/_client.py` | `request` / `request_stream` 에서 `files` 파라미터 + `FileInput` import 제거 |
| 9 | `sdk/llm_request/__init__.py` | 4 진입점에서 `files` 파라미터 제거 |

## 응답 shape

```jsonc
{
  "result": {...},
  "meta": {
    "source", "cache_hit", "cache_key", ...,
    "file_meta": [
      {"file_name": "x.pdf", "file_size": 12345, "file_mtime": 1700000000,
       "mime_type": "application/pdf", "extension": ".pdf",
       "file_path": "abc.pdf", "resolved_via": "primary"},
      {"file_name": "y.pdf", ..., "file_path": "new.pdf",
       "resolved_via": "fallback"}    // stale path → check_cache → 2 차 fetch 성공
    ]
  }
}
// resolved_via 값: "primary" | "fallback" — Phase 3 에서 "upload" 제거 (Method 1 폐기)
```

## 호환성 — BREAKING CHANGE

`LLMRequestBody.files` 제거 + `extra="forbid"` 로 옛 SDK 의 `files=[..]` 호출은 명시적 422 ValidationError.

| | 옛 SDK (`files=[FileInput]`) | 새 SDK (`stored_files=[StoredFile]`) |
|---|---|---|
| **pre-Phase-2 Service** (stored_files 필드 모름) | ✅ Method 1 동작 | ⚠️ silent drop (Pydantic 기본 `extra="ignore"`) |
| **Phase 2 Service** (stored_files 받지만 files 도 받음) | ✅ Method 1 동작 | ✅ 정상 동작 |
| **새 Service (Phase 3)** | ❌ 422 ValidationError (`extra="forbid"`) | ✅ 의도된 동작 |

→ **agent (ESG 등) 측 코드 마이그레이션 필수**. SDK + Service + agent **atomic 동시 배포** 권장.

## 설계 결정 (Spec §3-§5)

| # | 결정 | 결과 |
|---|------|------|
| §3 | bytes 전송 위치 | agent → file-manager 직접 (llm-request 우회) — 네트워크 1 hop 단축 |
| §4 | bytes fetch 시점 | 4·5 단계 leader 분기 (`_process_inline` / `_upload_and_cache_file`) — 기존 standby dedup 자동 활용 |
| §5 | files 인자 정책 | 폐기 + `extra="forbid"` 로 명시적 차단 (silent drop 회피) |
| §5 | `register_or_get` 위치 | file-manager SDK (PR 1) — agent 가 직접 호출, llm-request 는 fetch 만 |
| §6 | 404 fallback | 기존 Phase 2 `check_cache` 헬퍼 추출 후 lazy fetch 경로에서 재사용 |

# 🧪 테스트

### 단위 / 통합

`pytest service/tests/ sdk/tests/ -v` → 전체 통과 (회귀 0).

핵심 신규 / 변경 테스트:
- `test_requests_model.py::TestLLMRequestBodyFilesRemoved` — `files` 인자 422 검증 (`extra="forbid"`)
- `test_file_resolver.py::TestResolveStoredFilesLazy` — fetch 안 함 / validators 만 호출 검증
- `test_resolver.py::TestProcessInlineLazyFetch` / `TestUploadAndCacheFileLazyFetch` — lazy fetch 동작
- `test_runner_standby.py::TestPhase3LazyFetchDedup::test_concurrent_stored_files_fetch_once` — 10 동시 요청 → `get_file` 1 회만 호출 검증
- `test_router_request.py` — `body.files` 제거 정합성

삭제 테스트:
- `test_file_register.py` (Method 1 폐기)

### End-to-end 검증

localhost llm-request + 192.7.9.45 file-manager 환경에서 sample.txt 로 흐름 검증:
- agent: `file_manager.register_or_get(bytes, meta)` → 6 필드 dict 받음
- llm-request 호출 (`stored_files=[StoredFile(**dict)]`) → `resolved_via="primary"` ✅
- bytes 재전송 없이 정상 응답

# 📄 문서

- `README_v2.md` — 파일 첨부 섹션을 `register_or_get` + `stored_files` 흐름으로 재작성. `StoredFile` 6 필드 표 / `resolved_via` enum (`primary` / `fallback`) / 프론트엔드 업로드 가이드 / 마이그레이션 표 갱신
- `debugging-guide.md` — Phase 3 트러블슈팅 추가 (422 / 동시 요청 dedup / lazy fetch 5xx / 옛 Service silent drop)
- `docs/21.method1-deprecation-lazy-fetch.md` — Spec
- `docs/22.phase3-plan.md` — 14 task 실행 plan

# 🔄 Agent 측 마이그레이션

별도 repo / 별도 PR 로 진행. ESG 등 agent 코드:

```python
from file_manager import FileManagerHandler
from llm_request import llm_request_async, StoredFile

fm = FileManagerHandler()
sf_dict = await fm.register_or_get(
    bytes_data=open("report.pdf", "rb").read(),
    metadata=fm.build_metadata("report.pdf"),
)
# bytes release 가능
response, meta = await llm_request_async(
    {"contents": "분석해줘", "model": "gemini-2.5-flash"},
    stored_files=[StoredFile(**sf_dict)],
)
```

같은 파일 N 회 질문 시 `sf_dict` 만 자기 DB 에 저장 → 다음 호출에 재사용. bytes 재적재 / 재전송 없음.
