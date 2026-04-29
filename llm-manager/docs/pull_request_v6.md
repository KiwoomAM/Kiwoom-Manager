# Pull Request — llm-request Phase 1: `file_meta` 응답 노출

> **Spec:** [docs/18.file-meta-response-exposure.md](./18.file-meta-response-exposure.md) Phase 1 블록
> **참고 스타일:** [pull_request_v2.md](./pull_request_v2.md)

# 📝 개요

`/v1/request` 응답의 `meta` dict 에 `file_meta` 필드를 추가한다. file-manager 가 부여한 `file_path` 가 호출자에게 노출되어 자기 DB 에 저장 후 재호출 (Phase 2) 등에 활용 가능.

# 🔗 관련 이슈

#527

# ✅ 변경 사항

## 변경 위치 (코어 +10 줄)

| # | 파일 | 위치 | 변경 |
|---|------|-----|------|
| 1 | `service/app/logic/runner.py` | `_build_live_meta` (live 경로) | `_dump_files_meta(prep.files)` 호출 → 결과 None 아니면 `meta["file_meta"]` 추가 |
| 2 | `service/app/logic/runner.py` | `prepare` 의 `cached_meta` (cache hit 경로) | `dc.cached.get("file_meta")` 가 None 아니면 `cached_meta["file_meta"]` 추가 |
| 3 | `service/app/logic/store.py` | `fetch_by_cache_key` (직접 조회 경로) | `doc.get("file_meta")` 가 None 아니면 `meta["file_meta"]` 추가 |
| 4 | `sdk/llm_request/models.py` | `RequestMeta` | `file_meta: list[dict] \| None = None` 필드 추가 |

## 응답 shape

```jsonc
// Before — files 유무 무관
{
  "result": {...},
  "meta": {"source", "cache_hit", "cache_key", "metadata", "collection"}
}

// After — files=[..] 호출
{
  "result": {...},
  "meta": {
    "source", "cache_hit", "cache_key", "metadata", "collection",
    "file_meta": [
      {"file_name": "report.pdf", "file_size": 12345, "file_mtime": 1700000000,
       "mime_type": "application/pdf", "extension": ".pdf",
       "file_path": "<file-manager 가 부여한 UUID.확장자>"}
    ]
  }
}

// After — files=None 호출
// file_meta 키 자체 부재 (MongoDB doc 의 file_meta 동작과 거울 매칭)
```

## 호환성

추가 필드 only — 기존 consumer 가 새 키를 무시하면 영향 없음.

| | 옛 SDK | 새 SDK |
|---|---|---|
| **옛 Service** | ✅ 정상 | ✅ 정상 (default `None`) |
| **새 Service** | ✅ 정상 (Pydantic v2 기본 `extra="ignore"`) | ✅ 의도된 동작 |

→ 자연 호환. SDK + Service atomic 동시 배포 권장 (시간 gap 회피).

# 🧪 테스트

`pytest service/tests/ sdk/tests/ -v` → **service 195 passed / SDK 3 passed** (회귀 0).

신규 테스트 9 개:
- `test_runner.py::TestBuildLiveMetaFileMeta` (2) — live 경로 files=None / files=[..]
- `test_runner.py::TestPrepareCachedMetaFileMeta` (2) — cache hit + file_meta 있는 doc / 옛 doc
- `test_store.py::TestFetchByCacheKeyFileMeta` (2) — 동형
- `test_router_request.py` 신규 (3) — endpoint 통합 (`/v1/request` cache hit + legacy / `/v1/fetch`)
- `sdk/tests/test_models.py::TestRequestMetaFileMeta` (3) — 필드 default / 받음 / extra ignore

# 📄 문서

- `README_v2.md` — `RequestMeta.file_meta` 필드 + 활용 예시 추가
- `debugging-guide.md` — `meta.file_meta` 부재 원인 3 가지 + SDK ↔ Service 매트릭스
- `docs/18.file-meta-response-exposure.md` — Spec (Phase 1 + Phase 2 모두 포함, Phase 1 만 본 PR 범위)

# Pull Request — llm-request Phase 2: `stored_files` 입력 지원

> **Spec:** [docs/18.file-meta-response-exposure.md](./18.file-meta-response-exposure.md) Phase 2 H1 블록
> **Pre-requisite:** Phase 1 ([pr_phase1.md](./pr_phase1.md)) merged.
> **참고 스타일:** [pull_request_v2.md](./pull_request_v2.md)

# 📝 개요

호출자가 Phase 1 응답의 `meta.file_meta[i]` 6 필드를 다시 입력으로 넘기면 (`stored_files=[StoredFile(...)]`), llm-request 가 file-manager 에서 bytes 만 fetch — bytes 재전송 비용 (네트워크 + CPU + 메모리) 회피.

`StoredFile` 은 `FileInput` 의 bytes 만 `file_path` 로 대체된 형태 (5 메타 + file_path). caller mental model 이 Phase 1 ↔ Phase 2 대칭.

# 🔗 관련 이슈

#527

# ✅ 변경 사항

## 핵심 흐름

```
SDK (llm_request_async) — files / stored_files 양쪽 받음
  └─ POST /v1/request {llm_input, files, stored_files}
       └─ runner.llm_request_async
            └─ _prepare_files(files, stored_files) → combined
                 ├── if stored_files: resolve_stored_files → validate_mime/size (G2)
                 ├── if files: validate_mime/size → register_files → mark resolved_via="upload"
                 └── combined = files + resolved (G4 결정적 순서)
            └─ 기존 파이프라인 (prepare → ... → finalize)
```

`resolve_stored_files`:
1. `get_file(file_path)` 1차 → bytes 만 사용 (caller 의 5 메타 그대로) → `resolved_via="primary"`
2. 404 → `check_cache(hashing 3)` → hit 시 `get_file(new_path)` → `resolved_via="fallback"` + warning log
3. 둘 다 miss → `FileResolveError` raise (502, error_type='file_resolve')
4. 비-404 (5xx / network) → 그대로 전파

## 변경 위치

| # | 파일 | 변경 |
|---|------|-----|
| 1 | `service/app/models/requests.py` | `StoredFile` 신설 (6 필드, extra="forbid") / `FileInput.resolved_via` / `LLMRequestBody.stored_files` |
| 2 | `service/app/core/errors.py` | `FileResolveError` 신설 (`LLMRequestServiceError` 상속, 502, 'file_resolve') |
| 3 | `service/app/logic/file_resolver.py` | 신설 — `resolve_stored_files`, `_resolve_one`. `FileInput(**ref.model_dump(), data=data, ...)` — caller 6 필드 신뢰, `get_file` metadata header 무시 |
| 4 | `service/app/logic/runner.py` | `_prepare_files` 헬퍼 신설 (4 진입점 공통 로직 추출). `llm_request_async` / `llm_request` / `llm_request_stream_async` / `llm_request_stream` 시그니처에 `stored_files` 인자 |
| 5 | `service/app/routers/request.py` | `handle_request` / `handle_request_stream` 두 핸들러 `body.stored_files` 전달 |
| 6 | `sdk/llm_request/models.py` | `StoredFile` 신설 (Service 와 동일) |
| 7 | `sdk/llm_request/_client.py` | `request` / `request_stream` JSON body 직렬화 (`s.model_dump(mode="json")`) |
| 8 | `sdk/llm_request/__init__.py` | 진입점 4 함수 시그니처 + `StoredFile` export |

## 응답 shape (Phase 1 기반 + `resolved_via`)

```jsonc
{
  "result": {...},
  "meta": {
    "source": "live", "cache_hit": false, ...,
    "file_meta": [
      {"file_name": "x.pdf", ..., "file_path": "abc.pdf",
       "resolved_via": "primary"},      // stored_files 1차 fetch 성공
      {"file_name": "y.pdf", ..., "file_path": "new.pdf",
       "resolved_via": "fallback"}      // stale → check_cache → 2차 fetch 성공
    ]
  }
}
// data-mode (files=...) 호출 시: resolved_via == "upload"
// 옛 Phase 1 doc 의 cache hit: resolved_via 키 부재 (자연 호환)
```

## 호환성

추가 인자 (`stored_files`) + 추가 필드 (`resolved_via`) only — 기존 호출자 무영향.

| | 옛 SDK | 새 SDK |
|---|---|---|
| **옛 Service** | ✅ 정상 | ⚠️ silent drop — atomic 동시 배포로 회피 |
| **새 Service** | ✅ 정상 | ✅ 의도된 동작 |

→ SDK + Service **atomic 동시 배포** (한 PR + 같은 CI/CD pipeline). 호출자 (ESG) 는 자기 deploy 시점에 SDK 만 픽업하면 됨.

## 설계 결정 (Phase 2 §3.2)

| # | 결정 | 결과 |
|---|------|------|
| Q2 | 새 인자 vs `FileInput` 확장 | (B) 신규 인자 `stored_files` |
| P2-1 | mixed 호출 | (a) 허용 — `combined = files + resolved` |
| P2-2 | fallback 알림 | (c) server log + `meta.file_meta[i].resolved_via='fallback'` |
| P2-3 | 둘 다 miss | (a) `FileResolveError` raise |
| P2-4 | `StoredFile` extra | (c) `forbid` (server-fill 필드 차단) |
| P2-5 | data-mode `resolved_via` | `"upload"` 항상 채움 |
| 후속 | StoredFile 4 → 6 필드 | caller mental model 대칭 / monitoring 시 어차피 6 필드 다 보유 |

# 🧪 테스트

### 단위 / 통합

`pytest service/tests/ sdk/tests/ -v` → service **211 passed** / SDK **7 passed** (회귀 0).

신규 테스트:
- `test_requests_model.py` (6) — StoredFile 6 필드 / forbid / FileInput.resolved_via / LLMRequestBody.stored_files
- `test_file_resolver.py` (4) — primary / fallback / 둘 다 miss / 비-404 그대로 전파
- `test_runner.py::TestPrepareFiles` (4) — files only / stored only / mixed / 둘 다 None
- `test_router_request.py` (2) — `/v1/request` / `/v1/request/stream` 라우터 spy
- `sdk/tests/test_models.py::TestStoredFileSDK` (2) — 6 필드 / forbid
- `sdk/tests/test_client_serialization.py` (2) — JSON body 직렬화 / None 시 키 부재

### End-to-end 검증

localhost llm-request + 192.7.9.45 file-manager 환경에서 sample.txt 로 검증:
- Phase 1 호출 → `resolved_via="upload"` ✅, file_path 부여
- Phase 2 호출 (받은 file_path 로) → `resolved_via="primary"` ✅, bytes 재전송 없이 정상 응답

# 📄 문서

- `README_v2.md` — `stored_files` 사용법 / `StoredFile` 모델 / `resolved_via` enum / mixed / `FileResolveError` 처리
- `debugging-guide.md` — `ServiceError(error_type='file_resolve')` / fallback / `StoredFile` ValidationError / 옛 doc 호환 / SDK ↔ Service 매트릭스 / file-manager 트러블슈팅
- `docs/18.file-meta-response-exposure.md` Phase 2 — Spec (Phase 1 + Phase 2)
- `docs/20.phase2-stored-files-plan.md` — 11 task 실행 plan