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

### [x] B. Path Traversal @ `packages/file-manager/service/app/store.py:84`
- **주장:** `get_file_bytes`가 `file_path`를 무검증으로 `Path(storage_dir) / file_path` 결합 → 임의 파일 조회
- **검증:**
  - merge 후 라우터는 `file_handler.read_file`을 사용 (`routers/files.py:23, 156`)
  - `file_handler/storage.py:32-38`의 `_resolve_safe_path`가 이미 경계 검사 중 (`startswith(root)`)
  - `store.py` 전체가 dead code 확인 (라우터·SDK 어느 쪽도 import 안 함)
- **수정/하위 상태:**
  - [x] B-1. `packages/file-manager/service/app/store.py` 삭제 완료 (Fix A의 stub 잔재 정리 스윕에 포함)
  - [-] B-2. `file_handler/storage.py` 의 `startswith` → `is_relative_to` 강화 — **드롭**. llm-request 는 사내망 전용 배포이고 `file_path` 는 file-manager 가 내부에서 UUID 로 생성·관리하는 값이라 외부 공격자 통제 불가 → 실전 위협 모델 성립 안 함. 이슈 raise 도 생략

### [x] C. Validation 순서 역전 @ `runner.py:438-441, 562-565`
- **주장:** 설계는 `validate_mime → validate_size → register_files` 순이나 실제는 register 선행 → 2GB 초과·미지원 MIME도 일단 업로드됨
- **검증:** 주장 정확. 세 함수 서로 데이터 의존성 없음 (`FileInput.mime_type/file_size`만 사용, `register_files`는 입력 그대로 반환). `validators.py` docstring도 "진입 직후 fail-fast (prepare·MDB 조회 이전)" 명시
- **수정:** [runner.py:438-441](../service/app/logic/runner.py#L438-L441) / [runner.py:562-565](../service/app/logic/runner.py#L562-L565) 두 블록 모두 `validate_mime → validate_size → register_files` 순으로 교정 완료

### [x] D. PDF splitter NamedTemporaryFile 처리 오류 @ `splitter.py:122, 161`
- **주장:** `delete=False`로 열린 상태에서 `fitz.save()` 호출 → Windows 100% 실패, Linux 손상 위험
- **검증:** 주장 정확. 이중 핸들 문제 — `tmp`가 연 fd와 `fitz.save`가 같은 경로를 여는 핸들이 공존. Windows는 공유 모드로 확정 실패, Linux는 대부분 성공하지만 이론적 위험 존재. 비동기/동시성 아닌 단일 호출 내부 문제. 상세: `docs/00.concepts.md` §NamedTemporaryFile과 이중 핸들 문제
- **수정:** 두 블록 모두 `tempfile.NamedTemporaryFile(...)` 직후 `tmp.close()` 한 줄 추가 ([splitter.py:123](../service/app/logic/file_processing/splitter.py#L123), [splitter.py:163](../service/app/logic/file_processing/splitter.py#L163)) — `delete=False`이므로 close해도 파일 유지, 이후 `save`가 같은 경로에 덮어씀

### [x] E. SDK 의존성 누락 @ `packages/llm-request/sdk/pyproject.toml`
- **주장:** `kiwoom-llm-handler` 미선언 상태로 `llm_handler` re-export → 런타임 ImportError
- **검증:** SDK 코드에서 `(from|import) llm_handler` **4건** 확인 (`sdk/llm_request/__init__.py:30-32`, `sdk/llm_request/aio.py:14`) — 전부 top-level, 모듈 로드 시 즉시 평가. 루트 `uv sync` 환경에선 service를 통해 llm-handler가 설치되어 "우연히" 돌아감. sdk 단독 설치(`pip install -e sdk`) / wheel 배포 시나리오에서는 ImportError 확정
- **수정:** [sdk/pyproject.toml](../sdk/pyproject.toml) `dependencies` 정리. (1) `kiwoom-llm-handler>=0.1.0` 추가 (경로는 `[tool.uv.sources]` 에 이미 설정됨), (2) sdk 코드에서 import 0건인 dead dep 3개(`kiwoom-redis-handler`, `kiwoom-file-manager`, `filetype`) 제거. `sources` 는 "어디서 가져올지 경로", `dependencies` 는 "이게 필요하다는 선언" 으로 역할이 다름 — sources는 손대지 않음

### [x] F. 바이너리 커밋 @ `packages/file-manager/service/storage/`
- **주장:** PDF 3개(92MB 포함)·M4A·PNG 커밋
- **검증:** `ls packages/file-manager/service/{storage,fixtures}/` → 두 디렉터리 모두 존재하지 않음. Fix A의 stub 잔재 정리 스윕에서 이미 제거됨 (8개 바이너리 일괄 삭제, `pull_request_v3.md` §Fix A #4 참고)
- **수정:** 별도 수정 없음 — Fix A에 흡수됨
- **follow-up:** Kiwoom-Rag 루트 `.gitignore` 에 `packages/*/service/storage/`, `packages/*/service/fixtures/` 재발 방지 패턴 추가 — 루트 파일 수정이라 범위·영향이 다름, 별도 처리

---

## ⚠️ Important — 아키텍처 준수

### [x] I-A1. `run_many` / `run_many_stream`이 `files` 미전달 @ `orchestrator.py:14-57`
> orchestrator 삭제로 해소 — `pull_request_v3.md § orchestrator 제거` 참고
### [x] I-A2. `llm_request_stream_async` 반환 타입 불일치 @ `runner.py:547-551`
- **주장:** 설계는 `tuple[AsyncIterator, dict]` 인데 구현은 `AsyncIterator[StreamChunk]` 만 — 스트림 호출자가 `cache_hit` 등 meta 정보 못 받음
- **결정:** tuple 반환으로 통일 (옵션 a). non-stream `(response, meta)` 과 패턴 일치. Breaking change 감수 — Kiwoom-Rag 내 외부 호출자 grep 결과 없음
- **수정:** 4개 레이어 전환 — runner(`async generator`→`async function` + inner generator), 라우터(`X-LLM-Meta` 헤더 + body chunk-only), SDK `_client.request_stream` (`httpx.stream()` ctx 수동 관리로 tuple 반환), SDK 공개 함수 2개(`llm_request_stream_async`/`llm_request_stream`). 테스트·노트북·README 병행 마이그레이션. 상세: `pull_request_v3.md § Fix G`
### [x] I-A3. `run_many_stream`에 `_type: "cache_hit"` 이벤트 누락 @ `orchestrator.py:74`
> orchestrator 삭제로 해소 — `pull_request_v3.md § orchestrator 제거` 참고
### [-] I-A4. SDK 과도한 re-export — ARCHITECTURE.md는 5개인데 전체 노출
> 사용자 결정: 의도된 설계. llm_handler 의 `call/stream/upload_file/delete_file/create_cache` 등을 SDK 사용자가 llm-request만 설치해도 바로 쓸 수 있도록 재노출하는 게 목적. 수정 불필요
### [-] I-A5. `fetch_by_cache_key`의 collection 하드코딩 @ `store.py:120-121`
> 사용자 결정: `agent_llm` 컬렉션만 사용할 계획. 다른 컬렉션 조회 요구사항 없음. 하드코딩 유지. 수정 불필요
### [x] I-A6. file-manager httpx 싱글톤 미설정 @ `providers.py:79`
- **검증:** 리뷰어 주장(매 호출마다 AsyncClient 생성·폐기)은 사실과 다름. [`FileManagerHandler`](../../file-manager/sdk/file_manager/handler.py#L60-L74)는 `__new__` + `_initialized` 패턴으로 싱글톤 — `fm = FileManagerHandler()` 를 매 요청마다 호출해도 동일 인스턴스 반환, 내부 `httpx.AsyncClient` 재사용. 커넥션 풀 정상
- **남은 개선:** 서버 종료 시 `fm.close()` 호출이 누락돼 있었음 (리소스 누수 가능성)
- **수정:** [main.py](../service/app/main.py) `lifespan` shutdown 단계에 `await FileManagerHandler().close()` 추가. top-level `from file_manager import FileManagerHandler` import

## ⚠️ Important — 코드 품질

### [-] I-Q1. 함수 내부 import 7곳 (CLAUDE.md 금지) @ `runner.py:499, 634` / `providers.py:23, 37, 60, 70-71`
> **거부** — `packages/llm-request/CLAUDE.md` 전체 검토 결과 "함수 내부 import 금지" 규정 없음. `Kiwoom-Rag/CLAUDE.md` 도 존재 안 함. 리뷰어 주장의 근거 확인 실패. 해당 7곳(runner.py의 `import asyncio` 2개, providers.py의 handler 초기화 import 5개)은 기능적으로 문제 없으므로 현 상태 유지
### [x] I-Q2. `AsyncClient` 싱글톤 경쟁 @ sdk `__init__.py:134-139` — `asyncio.Lock` 필요
- **주장:** `_get_client()` 의 check-then-act 경쟁 — 동시 첫 호출 시 AsyncClient 중복 생성 + 리소스 누수 가능
- **검증:** 현재 구조(싱글 async 라우터)에선 실제 race 확률 낮지만, `AsyncClient` 생성자 내부에 await 추가 / `uvicorn --workers N` / sync 라우터 스레드풀 등 시나리오에서 터질 여지 있음. 방어 코드로 타당
- **수정:** [sdk/__init__.py](../sdk/llm_request/__init__.py) — `_instance_lock = asyncio.Lock()` 모듈 레벨 추가, `_get_client()` 를 `async def` 로 변환 + double-checked locking. 호출자 4곳(`llm_request_async`, `llm_request_stream_async`, `done_check`, `fetch_by_cache_key`) 에 `await` 추가. sync wrapper(`llm_request`, `llm_request_stream`) 는 `asyncio.run` 안에서 async 버전 호출하므로 변경 없음. 개념 정리: `docs/00.concepts.md` §"Race Condition, Lock, Single-flight / Standby"
### [x] I-Q3. `AsyncClient.aclose()` 미호출 @ `_client.py:24`
- **주장:** `sdk/_client.py` 에 `async def close(self)` 정의는 있지만 어디서도 호출 안 함. 프로세스 종료 시 `ResourceWarning: unclosed <AsyncClient>` + 커넥션 풀 리소스 누수
- **수정:** [sdk/__init__.py](../sdk/llm_request/__init__.py) 에 공개 `async def close()` 함수 추가 (싱글톤 `_instance.close()` 호출 + `_instance = None` 리셋). `__all__` 에 `"close"` 추가하여 `from llm_request import close` 사용 가능. [README_v2.md](../README_v2.md) 공개 API 표에 `close` 행 추가 + 주의사항에 "agent 프로세스 종료 전 `close()` 호출" 섹션 추가
### [x] I-Q4. Async 함수에서 sync Redis 사용 @ `dedup.py` — 이벤트 루프 블로킹
- **검증:** `redis-handler` 가 sync-only (`async/AsyncRedis/aioredis` 0건). `dedup.py` 의 `async def` 함수 3곳 (`try_acquire_standby`, `wait_for_standby_release`, `release_standby`) 이 raw `client.set/get/delete` 직접 호출. 같은 워커의 다른 코루틴이 5ms씩 못 깨어남. LLM 호출(2~30초) 대비 영향 0.02% 수준이지만 Redis 응답 느려질 때 비선형 증폭 위험
- **수정:** [dedup.py](../service/app/logic/file_processing/dedup.py) 3곳 모두 `await asyncio.to_thread(client.<method>, ...)` 로 감쌈. `import asyncio` 는 이미 존재해서 추가 불필요. `redis-handler` 는 손대지 않음 (다른 패키지) — sync 인터페이스가 미래에 헬퍼로 정리되어도 동일 패턴 유효
### [x] I-Q5. `cache_store.py`에서 `BaseJsonStore` private 직접 호출 (116-161)
- **검증:** `BaseJsonStore` ([`packages/redis-handler/redis_handler/base_store.py`](../../redis-handler/redis_handler/base_store.py)) 의 docstring (L13-23) 자체가 "**자식 클래스 안에서 `self._make_key/_get_json/...` 호출**" 을 정상 패턴으로 명시. 외부 모듈 함수에서 `_store._make_key(...)` 직접 호출은 디자인 위반. redis-handler 측 변경은 불필요 (옵션 (a) public helper 추가는 base_store가 의도한 캡슐화 방향과 역행)
- **수정:** [cache_store.py](../service/app/logic/file_processing/cache_store.py) 재구성 — Files API/Context Cache CRUD 함수 6개를 `FileCacheStore(BaseJsonStore)` 클래스 메서드로 이동, 기존 모듈 함수는 호출자(resolver.py 등) 호환을 위한 단순 위임으로 유지. SETNX 는 별개 — `dedup.py` 가 BaseJsonStore 우회하고 raw client 사용 중이며, 이는 동기화 primitive 라 JSON 저장소 추상화에 안 맞음 (I-Q4 영역에서 처리)
### [x] I-Q6. `_estimate_non_file_bytes`의 `except Exception: return 0` @ `resolver.py:70-71`
- **주장:** silent failure — 예외 발생해도 로그 없이 0 반환. 추적 불가 + inline 한도 계산이 낙관적이 됨 (파일이 inline pool 에 더 많이 들어가 Gemini request body 한도 초과 위험)
- **결정:** silent 해소가 본질. `logger.exception(...)` 추가, 동작(return 0) 유지. 이유: `json.dumps(llm_input, default=str)` 실패 자체가 극히 드문 edge(재귀 참조 등) — 이 상태면 llm_input 자체가 비정상이라 LLM 호출도 어차피 실패할 가능성이 높음. fallback 값(0 vs 한도값)을 바꿔도 실익 미미. split 은 Files API 경로의 개별 PDF 50MB 초과 대응이라 별개 레이어
- **수정:** [resolver.py:68-75](../service/app/logic/file_processing/resolver.py#L68-L75) — `except Exception:` 블록에 `logger.exception(...)` 호출 추가. 로그 메시지에 실패 원인·영향·추적 힌트 포함
### [x] I-Q7. `create_context_cache` model 타입 불일치 @ `resolver.py:241`, `uploader.py:67`
- **검증:** 호출 체인 `resolver._create_and_save_context_cache(model: str | None)` → `uploader.create_context_cache(model: str)` 에서 타입 좁아짐. resolver는 `llm_input.get("model")` 받아서 None 가능. context_cache 생성이 LLM 호출보다 **먼저** 일어나므로 model None일 때 Gemini Context Cache API에서 모호한 에러로 먼저 터질 수 있음
- **수정:** (1) `_create_and_save_context_cache` 시그니처를 `model: str | None` → `model: str` 로 변경 (uploader와 일치). (2) `_process_files_api` 의 context_cache 생성 분기 진입 직전에 `if model is None: raise ValueError(...)` 추가 — fail-fast로 명확한 한국어 에러 메시지 제공
### [-] I-Q8. `Base64Field` 정의 중복 @ `models.py:21-25` ↔ `requests.py:13-17`
- **주장:** DRY 위반 — `Base64Field` 가 SDK 와 Service 양쪽에 같은 정의로 존재
- **결정:** **의도된 중복 유지**. SDK 와 Service 는 독립 파이썬 패키지이고 공유 모듈(e.g. `kiwoom-llm-request-common`) 을 만들 정도는 아님. 직렬화 동작이 SDK↔Service HTTP 계약의 일부라 양쪽이 **같은 동작을 보장**하는 게 핵심
- **수정:** 코드 변경 없음. [sdk/models.py](../sdk/llm_request/models.py) / [service/models/requests.py](../service/app/models/requests.py) 양쪽 Base64Field 정의 위에 "⚠️ 의도된 중복: 수정 시 양쪽 반드시 동기화" 주석 추가. [pull_request_v3.md](pull_request_v3.md) 참고 사항에 한 줄 기록
### [x] I-Q9. `run_many` 예외 시 dangling task @ `orchestrator.py:38-42`
> orchestrator 삭제로 해소 — `pull_request_v3.md § orchestrator 제거` 참고
### [x] I-Q10. 미사용 `FileChecksum` TypedDict @ `logic/types.py`
- **주장:** `FileChecksum` TypedDict 가 구 설계 잔재로 남아있고 아무 데서도 사용 안 됨
- **검증:** Grep 양방향 (`from app.logic.types import`, `from .types import`, 파일명 직접 참조 등) 전 llm-request 트리 재확인 결과 실제 import **0건**. `test_keying.py:171` 의 `TestFileChecksumsInSubset` 은 클래스 이름만 유사 (실제 타입 import 안 함, `file_checksums` 리스트 문자열만 다룸). ARCHITECTURE.md · docs/ 의 언급은 설계 레거시 (사용자 방침: 문서 정리는 별도)
- **수정:** `service/app/logic/types.py` 파일 통째로 삭제. 파일에 `FileChecksum` 외 다른 symbol 없었음 — 이 파일이 사라져도 import 에러 없음
### [x] I-Q11. 테스트 누락 — `test_resolver.py`, validators, classify, `_process_*`
- **수정:** 신규 테스트 파일 3개 추가 (총 **29 tests** — 골든 패스 + 핵심 분기 커버)
  - [test_validators.py](../service/tests/logic/file_processing/test_validators.py) — `validate_mime_types` (whitelist 통과/거부/빈/부분거부) + `validate_file_sizes` (경계/초과/0/빈) — **10 tests**
  - [test_resolver.py](../service/tests/logic/file_processing/test_resolver.py) — `classify_files` (비디오 강제분리/20MB/50MB 한도/한도초과/빈) + `_estimate_non_file_bytes` (정상/예외) + `_process_inline` (정상/빈) + `resolve_files` (빈/model None+ttl raise(I-Q7 회귀)/ttl없음OK/승급정책) — **13 tests**
  - [test_file_register.py](../service/tests/logic/test_file_register.py) — `register_files` (cache hit/miss + upload/check_cache 예외 wrap/upload 예외 wrap/빈/혼합 hit-miss) — **6 tests**

## ⚠️ Important — 호환성

### [x] I-C1. `packages/llm-request/pyproject.toml` 설치 패키지 → 워크스페이스 루트 변경 안내
- **주장:** v0.1.0 에서 `pip install -e packages/llm-request` 로 SDK 설치하던 사용자가 v0.2.0 에 같은 명령을 실행하면 **빈 워크스페이스 루트** 만 설치되어 SDK 기능 없음. 이주 안내 필수
- **수정:** [README_v2.md §12 v0.1.0 → v0.2.0 마이그레이션](../README_v2.md) 의 변경 요약 표 바로 뒤에 "⚠️ 설치 경로 변경 주의" 경고 블록 추가 — (1) 증상 설명, (2) 새 경로 `pip install -e packages/llm-request/sdk` 또는 루트 `uv sync`, (3) 구 버전 환경에서 업그레이드 시 `pip uninstall kiwoom-llm-request` 선행 안내
### [x] I-C2. docker-compose MongoDB DB/컬렉션 이름 정합 (`kw_ai`/`file_cache` vs `kw_ai_dev`/`file_manager`)
> Fix A 에 흡수 — stub 폐기하고 v1 기준 `kw_ai` / `file_cache` 로 통일 (`pull_request_v3.md § Fix A` 참고)
### [x] I-C3. `packages/file-handler/` 고아 여부 결정
> Fix A 에 흡수 — v1 file-manager 가 `file-handler` 를 스토리지 위임 대상으로 사용 중. "유지" 결론 (`pull_request_v3.md § Fix A` 참고)
### [x] I-C4. `kiwoom-file-manager` 버전 역행 (1.0.0 → 0.2.0)
> Fix A 에 흡수 — main 의 v1 (1.0.0) 을 채택하면서 역행 자동 해소 (`pull_request_v3.md § Fix A` 참고)

---

## 💡 Suggestions

### [-] S1. SDK 기본 URL 내부망 IP `192.7.9.45` 하드코딩 → env 필수화 @ `sdk/llm_request/__init__.py:131`
- **주장:** 사내망 IP 가 fallback 으로 하드코딩돼 있어 외부 사용자에게 혼란
- **검증:** 현재 구조는 `os.getenv("LLM_REQUEST_URL", "http://192.7.9.45:8101")` — **env 우선, IP fallback**. env `LLM_REQUEST_URL` 설정 시 그 값이 이김
- **결정:** 현 상태 유지 (수정 없음). Kiwoom-Rag 는 사내망 전용 배포 환경이고 fallback IP 가 있어 로컬 개발·테스트가 편함. 외부 환경에서도 env 로 override 가능. 리뷰어 제안(IP 제거 + env 필수화 / localhost fallback) 거부
### [x] S2. 설계 문서 `ingest.py` vs 구현 `file_register.py` 이름 정합
- **주장:** 설계 문서는 `ingest.py` / `ingest_files` 이름 사용, 실제 구현은 `file_register.py` / `register_files` — 문서·코드 불일치
- **결정:** 설계 문서 쪽을 구현 이름에 맞춤 (mechanical rename)
- **수정:** 2개 설계 문서 일괄 치환 —
  - [00.code-review-walkthrough.md](00.code-review-walkthrough.md) 2곳 (`ingest.py`, `ingest_files`)
  - [10.file-manager-bytes-passthrough.md](10.file-manager-bytes-passthrough.md) 9곳 (`ingest.py` 3곳 + `ingest_files` 6곳)
  - 총 11곳 — `ingest.py` → `file_register.py`, `ingest_files` → `register_files`. TODO 체크리스트의 과거 완료 기록(`- [x] .../ingest.py 신규 ...`)도 함께 갱신 — 문서 전반에 현재 이름만 남기는 정합성 우선
### [x] S3. `context_cache_ttl=0` falsy 취급 → `is not None and > 0` 명시
- **주장:** `if context_cache_ttl:` 이 `0` 을 False 로 처리. 사용자가 의도적으로 `0` 을 "비활성" 의미로 주면 암묵적으로만 동작 — 명시적이지 않음
- **수정:**
  - [resolver.py:369-373](../service/app/logic/file_processing/resolver.py#L369-L373) — 조건을 `ctx_cache_active = context_cache_ttl is not None and context_cache_ttl > 0` 로 로컬 변수 추출, 아래 두 `if` 조건에서 재사용. "None 도 0 도 아닌 양수만" 이 코드로 명시됨
  - [README_v2.md Timings 표 `context_cache_ttl` 행](../README_v2.md) — "`None` 또는 `0` 이면 비활성, 양수(`>0`) 값만 생성 트리거" 로 설명 보강
### [-] S4. `NamedTemporaryFile(delete=False)` → `with ... delete=True` @ `resolver.py:163-170`
- **주장:** 스타일 제안 — `delete=True` + `with` 문이 정리 자동화 측면에서 깔끔
- **검증:** 해당 위치는 이미 `tmp.close()` 후 `upload_file` 호출 + `finally os.unlink(tmp.name)` 으로 정리 보장 중 (Fix D 분석에서 확정). 리뷰어 제안대로 `delete=True` 로 바꾸면 `with` 블록 종료 시점까지 fd 가 열려있어 upload 중 이중 핸들 문제 재현 — Windows 에서 D 블로커와 동일한 `PermissionError` 위험
- **결정:** 거부. 현재 패턴(`delete=False` + 명시적 `close` + `finally os.unlink`)이 Windows 호환성·리소스 정리 둘 다 만족. 수정 없음

---

## ✅ 잘된 점 (원본 리뷰 기록)

- `dedup.py` standby + exponential backoff
- `validators.py` MIME whitelist frozenset, 2GB 상수화
- `compute_files_api_ttl` Gemini 만료 기반 동적 TTL
- `keying.py` file_checksums 주입 + volatile 정규화
- FastAPI 미들웨어 `request_id` uuid 전파
- `splitter.py` 재귀 깊이 제한 `_MAX_DEPTH=3`

---

# 📦 v5 추가 — 4-에이전트 병렬 리뷰 대응

> 대응 문서: `pull_request_v5.md`

## 🚨 Critical

### [x] C-1. `tools` / `tool_config` 비대칭 silent drop @ `runner.py:321-323`
- **주장:** `_apply_resolved` cache 경로가 request 의 `tools` / `tool_config` 를 pop 하지만 `uploader.create_context_cache` 는 해당 파라미터를 받지도 않아 cache 에 안 들어감 → caller 의 도구 정의가 silent drop
- **반영:** 권장안(주입) 채택. chain 5단계 (uploader → cache_store key → resolver 4함수 → runner 호출부 + README) 모두 연결. 회귀 방지 테스트 5개 추가 (`test_uploader.py` 2 + `test_cache_store.py` 3)

## ⚠️ Important

### [x] I-1. `contents` 비-`str`/`list` 타입 silent 소실 @ `runner.py:307-308`
- **주장:** `dict` / `int` / `bytes` / `None` 입력 시 `text_contents = []` 로 silent drop
- **반영:** `else: raise TypeError(...)` 로 fail-fast. 회귀 방지 `TestApplyResolvedContentsTypeGuard` (5 케이스)

### [-] I-2. 하드코딩된 IP — `verify_all.ipynb`
- **주장:** `LLM_REQUEST_URL = "http://192.7.9.45:8101"` 가 "No Hardcoding" 위반
- **결정:** 거부 — 의도된 사내망 fallback (v3 §S1 결론과 동일 정책). quickstart 는 zero-config 우선

### [x] I-3. `pyproject.toml` readme — 삭제보다 경로 수정이 근본
- **주장:** `readme = "README_v2.md"` 로 수정이 정답. 메타데이터 삭제는 PyPI 디스커버리 약화
- **반영:** `readme = "README_v2.md"` 로 명시

### [x] I-4. 테스트 공백 — 순서·mutation·call-site wiring
- **주장:** 순서 invariant 미검증 (`len()` / `[-1]` 만), shallow copy mutation 안전성 미검증, `_apply_resolved` 호출 3군데 wiring 통합 테스트 없음
- **반영:** `test_runner.py` 신규 클래스 3개 — `TestApplyResolvedOrder` (각 위치 객체 동일성 어서션), `TestApplyResolvedMutationSafety` (cache/non-cache 양쪽), `TestBuildServerCallInput` (no-files / files+cache wiring)

## 💡 Suggestions

### [x] S-1. `# uri_parts 제외` 인라인 주석 제거
- **반영:** 권장안 채택. docstring 과 중복 → drift 방지로 제거

### [x] S-2. docstring 외부 API 단정에 레퍼런스 추가
- **주장:** "Gemini INVALID_ARGUMENT" 는 외부 API 거동 단정 → spec 링크/티켓 각주 필요
- **반영:** `INVALID_ARGUMENT` → "Bad Request" 약화 + docstring 끝에 `근거: packages/llm-handler/CLAUDE.md Gotchas` 출처 명시 (`runner.py:341` 의 `상세: docs/inline-vs-files-api-strategy.md` 와 동일 패턴)

### [x] S-3. `test_runner.py` 모듈 docstring 위임
- **반영:** 모듈 docstring 을 `로직 설명은 _apply_resolved docstring 참고.` 한 줄로 간소화

### [-] S-4. `build_metadata` `@staticmethod` 노출
- **결정:** 별도 PR — `packages/file-manager/sdk` 시그니처 변경이라 다른 사용처 영향 검토 필요. 미래 안전성 issue → follow-up
