# PR #468 Review Checklist

> 원 리뷰: https://github.com/KIWOOMxSNU/Kiwoom-Rag/pull/468
> 작성: 2026-04-21
> 상태 표기: `[ ]` 미착수 / `[~]` 검증중 / `[x]` 완료 / `[-]` 검증 결과 수정 불필요

각 항목은 **리뷰어 주장 → 실제 검증 → 결정/액션** 순으로 기록한다.

---

## 🚨 Blockers

### [x] A. Main과의 대규모 충돌
- **주장:** 8개 파일 add/add·content 충돌, file-manager 설계가 main과 이질적
- **검증:** merge 완료 (`ec3eb6d Merge remote-tracking branch 'origin' into feat/llm_request`, `50a98d5 file-manager연결`)
- **결정:** main의 file-handler 기반 설계로 수렴 (완료)

### [~] B. Path Traversal @ `packages/file-manager/service/app/store.py:84`
- **주장:** `get_file_bytes`가 `file_path`를 무검증으로 `Path(storage_dir) / file_path` 결합 → 임의 파일 조회
- **검증:**
  - merge 후 라우터는 `file_handler.read_file`을 사용 (`routers/files.py:23, 156`)
  - `file_handler/storage.py:32-38`의 `_resolve_safe_path`가 이미 경계 검사 중 (`startswith(root)`)
  - `store.py` 전체가 dead code로 보임 (라우터·SDK 어느 쪽도 import 안 함)
- **남은 액션:**
  - [ ] B-1. `packages/file-manager/service/app/store.py` 삭제 검토 (완전 미사용 최종 확인 후)
  - [ ] B-2. `file_handler/storage.py`의 `startswith` → `is_relative_to`로 강화 (`/app/data` vs `/app/data_leak` 우회 가능성 제거)

### [x] C. Validation 순서 역전 @ `runner.py:438-441, 562-565`
- **주장:** 설계는 `validate_mime → validate_size → register_files` 순이나 실제는 register 선행 → 2GB 초과·미지원 MIME도 일단 업로드됨
- **검증:** 주장 정확. 세 함수 서로 데이터 의존성 없음 (`FileInput.mime_type/file_size`만 사용, `register_files`는 입력 그대로 반환). `validators.py` docstring도 "진입 직후 fail-fast (prepare·MDB 조회 이전)" 명시
- **수정:** [runner.py:438-441](../service/app/logic/runner.py#L438-L441) / [runner.py:562-565](../service/app/logic/runner.py#L562-L565) 두 블록 모두 `validate_mime → validate_size → register_files` 순으로 교정 완료

### [x] D. PDF splitter NamedTemporaryFile 처리 오류 @ `splitter.py:122, 161`
- **주장:** `delete=False`로 열린 상태에서 `fitz.save()` 호출 → Windows 100% 실패, Linux 손상 위험
- **검증:** 주장 정확. 이중 핸들 문제 — `tmp`가 연 fd와 `fitz.save`가 같은 경로를 여는 핸들이 공존. Windows는 공유 모드로 확정 실패, Linux는 대부분 성공하지만 이론적 위험 존재. 비동기/동시성 아닌 단일 호출 내부 문제. 상세: `docs/00.concepts.md` §NamedTemporaryFile과 이중 핸들 문제
- **수정:** 두 블록 모두 `tempfile.NamedTemporaryFile(...)` 직후 `tmp.close()` 한 줄 추가 ([splitter.py:123](../service/app/logic/file_processing/splitter.py#L123), [splitter.py:163](../service/app/logic/file_processing/splitter.py#L163)) — `delete=False`이므로 close해도 파일 유지, 이후 `save`가 같은 경로에 덮어씀

### [ ] E. SDK 의존성 누락 @ `packages/llm-request/sdk/pyproject.toml`
- **주장:** `kiwoom-llm-handler` 미선언 상태로 `llm_handler` re-export → 런타임 ImportError
- **검증:** TODO
- **남은 액션:** 의존성 추가 + 중요 이슈 #4 (re-export 범위 축소)와 묶어서 처리

### [ ] F. 바이너리 커밋 @ `packages/file-manager/service/storage/`
- **주장:** PDF 3개(92MB 포함)·M4A·PNG 커밋
- **검증:** TODO (merge 후에도 남아있는지)
- **남은 액션:** 제거 + `.gitignore`에 `packages/*/service/storage/` 추가

---

## ⚠️ Important — 아키텍처 준수

### [ ] I-A1. `run_many` / `run_many_stream`이 `files` 미전달 @ `orchestrator.py:14-57`
### [ ] I-A2. `llm_request_stream_async` 반환 타입 불일치 @ `runner.py:547-551`
### [ ] I-A3. `run_many_stream`에 `_type: "cache_hit"` 이벤트 누락 @ `orchestrator.py:74`
### [ ] I-A4. SDK 과도한 re-export — ARCHITECTURE.md는 5개인데 전체 노출
### [ ] I-A5. `fetch_by_cache_key`의 collection 하드코딩 @ `store.py:120-121`
### [ ] I-A6. file-manager httpx 싱글톤 미설정 @ `providers.py:79`

## ⚠️ Important — 코드 품질

### [ ] I-Q1. 함수 내부 import 7곳 (CLAUDE.md 금지) @ `runner.py:499, 634` / `providers.py:23, 37, 60, 70-71`
> CLAUDE.md 실제 금지 문구인지 먼저 확인 필요
### [ ] I-Q2. `AsyncClient` 싱글톤 경쟁 @ sdk `__init__.py:134-139` — `asyncio.Lock` 필요
### [ ] I-Q3. `AsyncClient.aclose()` 미호출 @ `_client.py:24`
### [ ] I-Q4. Async 함수에서 sync Redis 사용 @ `dedup.py` — 이벤트 루프 블로킹
> 본 PR 범위인지, follow-up 이슈인지 결정 필요
### [ ] I-Q5. `cache_store.py`에서 `BaseJsonStore` private 직접 호출 (116-161)
### [ ] I-Q6. `_estimate_non_file_bytes`의 `except Exception: return 0` @ `resolver.py:70-71`
### [ ] I-Q7. `create_context_cache` model 타입 불일치 @ `resolver.py:241`, `uploader.py:67`
### [ ] I-Q8. `Base64Field` 정의 중복 @ `models.py:21-25` ↔ `requests.py:13-17`
### [ ] I-Q9. `run_many` 예외 시 dangling task @ `orchestrator.py:38-42`
### [ ] I-Q10. 미사용 `FileChecksum` TypedDict @ `logic/types.py`
### [ ] I-Q11. 테스트 누락 — `test_resolver.py`, validators, classify, `_process_*`

## ⚠️ Important — 호환성

### [ ] I-C1. `packages/llm-request/pyproject.toml` 설치 패키지 → 워크스페이스 루트 변경 안내
### [ ] I-C2. docker-compose MongoDB DB/컬렉션 이름 정합 (`kw_ai`/`file_cache` vs `kw_ai_dev`/`file_manager`)
### [ ] I-C3. `packages/file-handler/` 고아 여부 결정
### [ ] I-C4. `kiwoom-file-manager` 버전 역행 (1.0.0 → 0.2.0)

---

## 💡 Suggestions

### [ ] S1. SDK 기본 URL 내부망 IP `192.7.9.45` 하드코딩 → env 필수화 @ `sdk/llm_request/__init__.py:131`
### [ ] S2. 설계 문서 `ingest.py` vs 구현 `file_register.py` 이름 정합
### [ ] S3. `context_cache_ttl=0` falsy 취급 → `is not None and > 0` 명시
### [ ] S4. `NamedTemporaryFile(delete=False)` → `with ... delete=True` @ `resolver.py:163-170`

---

## ✅ 잘된 점 (원본 리뷰 기록)

- `dedup.py` standby + exponential backoff
- `validators.py` MIME whitelist frozenset, 2GB 상수화
- `compute_files_api_ttl` Gemini 만료 기반 동적 TTL
- `keying.py` file_checksums 주입 + volatile 정규화
- FastAPI 미들웨어 `request_id` uuid 전파
- `splitter.py` 재귀 깊이 제한 `_MAX_DEPTH=3`
