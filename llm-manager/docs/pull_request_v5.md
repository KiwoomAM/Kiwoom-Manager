# Pull Request #468 v5 — 4-에이전트 병렬 리뷰 대응


## Critical

### C-1. `tools` / `tool_config` 비대칭 silent drop

**주장:** [runner.py:321-323](../service/app/logic/runner.py#L321-L323) 의 `_apply_resolved` 가 cache 경로에서 request 의 `tools` / `tool_config` 를 pop 하지만, [uploader.py:84-93](../service/app/logic/file_processing/uploader.py#L84-L93) 의 `create_context_cache` 는 `_aio_create_cache` 로 `model` / `system` / `ttl` 만 전달 → cache 에 안 들어감. caller 가 지정한 tool-calling 이 경고 없이 사라짐. 권장: `create_context_cache` 에서 `tools` / `tool_config` 도 cache 에 주입.

**반영:** 권장안(주입) 채택. chain 5단계를 한 호흡으로 연결.

1. `uploader.create_context_cache` 시그니처에 `tools` / `tool_config` 추가 → `_aio_create_cache` 로 전달
2. `cache_store.make_context_cache_key` 의 키 재료에 `tools` / `tool_config` 포함 — 같은 파일·모델·system 이라도 tools 다르면 별도 cache 로 분리 (오염된 HIT 방지)
3. `resolver._create_and_save_context_cache` / `_process_files_api` / `resolve_files` / `resolve_files_after_404` 모두 파라미터 통과
4. `runner._build_server_call_input` 이 `llm_input.get("tools" / "tool_config")` 추출해 `resolve_files` 로 전달 (404-retry 2곳 포함)
5. `README.md` Context Cache 섹션 → 5-key cache makeup (file_checksums + model + system_instruction + tools + tool_config) 명시

회귀 방지 테스트:
- `test_uploader.py::TestCreateContextCache` — `tools` / `tool_config` 가 `_aio_create_cache` 까지 전달되는지 + None 기본값 명시 전달 (2)
- `test_cache_store.py::TestMakeContextCacheKey` — `tools` 다르면 다른 key + `tool_config` 다르면 다른 key + `None` 과 `[]` 는 동일 key (3)

---

## Important

### I-1. `contents` 비-`str`/`list` 타입 silent 소실

**주장:** `runner.py:307-308` — `contents` 가 `dict` / `int` / `bytes` / `None` 이면 `text_contents=[]` 로 silent drop. caller 입력 오류 가리는 silent data loss. 권장: `else: raise TypeError(...)`.

**반영:** 권장안 채택. `else` 분기에 명시적 `TypeError` raise. 회귀 방지 `TestApplyResolvedContentsTypeGuard` 5 케이스 (`dict` / `None` / `int` / `bytes` + missing-key fallback).

### I-2. 하드코딩된 IP — `verify_all.ipynb`

**주장:** `LLM_REQUEST_URL = "http://192.7.9.45:8101"` 가 global CLAUDE.md "No Hardcoding" 위반. 권장: localhost 또는 `os.getenv(..., "http://localhost:8101")`.

**반영:** 미수정. 사내망 안에서 quickstart로 사용되는 함수되는 함수들을 직접 실행해보는 목적으로 항상 해당 서버로 접속하여 테스트해봐야 하여 하드코딩 필요. SDK 의 `os.getenv("LLM_REQUEST_URL", "http://192.7.9.45:8101")` 와 동일 정책 . 

### I-3. `pyproject.toml` readme — 삭제 대신 경로 수정

**주장:** "README → README_v2 리네이밍 잔재" 라면 `readme = "README.md"` 가 정답. 메타데이터 삭제는 PyPI 디스커버리 약화.

**반영:** 권장안 채택. `readme = "README.md"` 로 명시.

### I-4. 테스트 공백

**주장 3건:**
- caller mutation 안전성 미검증: `dict(llm_input)` shallow copy → 404-retry 경로 (`runner.py:404`, `runner.py:532`) 에서 `prep.llm_input` 재사용하므로 회귀 위험
- 순서 invariant 미검증: docstring 은 "inline + uri + text" 주장하지만 `len()` / `contents[-1]` 만 확인 → `position 0 == inline`, `position 1 == uri` 어서션 필요
- call-site wiring 미검증: `_apply_resolved` 호출 3군데 (`_build_server_call_input`, 404-retry ×2) 통합 테스트 없음

**반영:** `test_runner.py` 신규 클래스 3개로 모두 커버.
- `TestApplyResolvedMutationSafety` (2) — `_apply_resolved` 가 원본 `llm_input` dict 를 변경하지 않는지 cache / non-cache 양쪽 검증
- `TestApplyResolvedOrder` (2) — `contents[0] is inline1`, `contents[1] is inline2`, `contents[2] is uri1` 등 각 위치 객체 동일성까지 어서션 (cache 경로는 uri 제외 변형)
- `TestBuildServerCallInput` (2) — `files=None` 경로 (llm_input 그대로 복사) + `files=[..]` + `cached_content` 경로 (cache wiring 검증)

---

## Suggestions

### S-1. `# uri_parts 제외` 인라인 주석 — docstring 과 중복

**주장:** drift 방지로 제거. "Default to writing no comments" 원칙 부합.

**반영:** 권장안 채택. 인라인 주석 제거.

### S-2. docstring 외부 API 단정에 레퍼런스 추가

**주장:** "Gemini INVALID_ARGUMENT" 는 외부 API 거동 단정 → spec 링크/티켓 각주 필요. 같은 파일 325/345 행에 `docs/inline-vs-files-api-strategy.md` 선례 있음.

**반영:** 권장안 채택. `INVALID_ARGUMENT` → "Bad Request" 로 응답 코드 단정 약화 + docstring 끝에 `근거: packages/llm-handler/CLAUDE.md Gotchas — "cached_content 사용 시 tools/system/tool_config 동시 전달 불가 ..."` 출처 명시. `runner.py:341` 의 `상세: docs/inline-vs-files-api-strategy.md §5, §7.2` 와 동일 패턴.

### S-3. `test_runner.py` 모듈 docstring 위임

**주장:** `test_runner.py` 모듈 docstring 이 `runner.py` docstring 과 근거 중복 → "근거는 `_apply_resolved` docstring 참고" 로 위임해 drift 방지.

**반영:** 권장안 채택. 모듈 docstring 을 `"""runner._apply_resolved 단위 테스트.\n\n로직 설명은 _apply_resolved docstring 참고.\n"""` 로 간소화.

### S-4. `build_metadata` `@staticmethod` 노출

**주장:** Notebook 의 `FileManagerHandler()` 가 향후 I/O 로 확장되면 quickstart 첫 셀이 깨짐. `build_metadata` 가 self 미사용이므로 `@staticmethod` 로 노출.

**반영:** 미수정. `packages/file-manager/sdk/file_manager/handler.py` 시그니처 변경이라 별도 PR 범위. 현재 깨진 게 아니라 미래 안전성 issue → follow-up.

---

## 테스트

`pytest service/tests/ -v` → **180 passed**. C-1 / I-1 / I-4 회귀 방지 테스트 9개 신규 추가.
