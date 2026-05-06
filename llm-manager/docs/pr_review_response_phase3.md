# PR Review Response — Phase 3 (Method 1 폐기 + Lazy Fetch)

각 항목 — 리뷰어 발췌 → 해결.

---

## Critical

### C1. dedup 주장이 inline 경로에선 거짓

> "70 동시 요청에서도 leader 1 명만 fetch" 라고 docstring 에 적혀 있지만, `try_acquire_standby` 는 `_process_files_api` 안에서만 적용되고 `_process_inline` 에는 없습니다 ... 작은 PDF/text/image 가 inline 분류되어 들어오면 동시 N 요청이 모두 file-manager 직격합니다.

→ **정정**. 현재 inline 로직은 dedup 과정을 진행하지 않아 Redis 를 사용하지 않음. 따라서 standby 기능이 없고, 새로 구현하려면 시간이 소요됨. inline 분기는 작은 파일 위주 (이미지/오디오 ≤20MB, PDF/text ≤50MB) 라 현재는 문제 없을 것으로 예상되어, 우선 files_api 처럼 대용량 파일 경로에만 dedup 적용. 주장을 **"files_api 경로 한정 dedup. inline 은 N 회 fetch 허용"** 으로 정정. 추후 메모리 압박이나 동일 inline 사이즈 파일 반복 질문 시나리오가 실측되면 그 때 반영.

**[file_resolver.py 모듈 docstring]**

before:
```
Phase 2 에선 여기서 file-manager fetch + 404 fallback 진행했으나, Phase 3 에선
fetch 자체를 4·5단계 (resolver.py) 로 이전 — `try_acquire_standby` 메커니즘이
fetch 까지 자동으로 dedup. 70 동시 요청에서도 leader 1 명만 fetch.
```

after:
```
Phase 2 에선 여기서 file-manager fetch + 404 fallback 진행했으나, Phase 3 에선
fetch 자체를 4·5단계 (resolver.py) 로 이전.

dedup 범위: files_api 경로만 — leader 가 업로드한 URI 가 Redis 에 저장되어
waiter 가 재사용. inline 경로는 결과 (bytes) 가 Redis 에 캐시되지 않아 dedup
효익 없음 → N 회 fetch 허용 (의도된 trade-off, 작은 파일 한정).
```

다른 수정 위치:
- [pull_request_v6.md](./pull_request_v6.md) 핵심 효과 / 핵심 흐름
- [README_v2.md](../README_v2.md) File Handler 도식
- [debugging-guide.md](../debugging-guide.md) "동시 요청 N 개" 섹션

---

### C2. `resolved_via="primary"` 가 검증 없이 박제됨

> placeholder 의 초기값을 "primary" 로 두면, `_reuse_files_api_uris` / standby waiter 분기 / files_api Redis HIT 케이스처럼 fetch 를 한 번도 안 한 경로도 응답·MDB 의 `file_meta[i].resolved_via="primary"` 로 영구 기록됩니다.

→ **수정**. [file_resolver.py:42](../service/app/logic/file_resolver.py#L42) 의 `resolved_via="primary"` 를 `None` 으로 변경. 실제 fetch 한 분기 (`_process_inline` / `_upload_and_cache_file` 의 `fetch_with_fallback` 직후) 에서만 `"primary"` 로 갱신. cache HIT (`_reuse_files_api_uris`) / standby waiter 분기는 `None` 유지 — 검증되지 않은 "primary" 박제 안 됨.

회귀 테스트 3 개 추가: [test_resolver.py::TestResolvedViaPropagation](../service/tests/logic/file_processing/test_resolver.py).

---

### C3. `FileResolveError` docstring 이 폐기된 복구 경로 안내

> "호출자가 catch 후 data-mode (files=[..]) 로 bytes 재업로드하는 것이 권장 복구 경로" — 이 PR 이 바로 그 files= 인자를 제거하고 extra="forbid" 로 422 강제합니다.

→ **수정**. [errors.py::FileResolveError](../service/app/core/errors.py) docstring 을 `register_or_get` 기반 복구 안내로 갱신. 폐기된 docs 참조 제거.

before:
```python
class FileResolveError(LLMRequestServiceError):
    """stored_files resolve 단계에서 file-manager 가 파일을 찾을 수 없음 (Phase 2).

    1차 get_file(file_path) 404 + 2차 check_cache(hashing 3 필드) miss →
    file-manager 에 정말로 없는 상태. 호출자가 catch 후 data-mode (files=[..])
    로 bytes 재업로드하는 것이 권장 복구 경로.

    docs/18.file-meta-response-exposure.md Phase 2 §6.2 참고.
    """
```

after:
```python
class FileResolveError(LLMRequestServiceError):
    """stored_files resolve 단계에서 file-manager 가 파일을 찾을 수 없음.

    1차 get_file(file_path) 404 + 2차 check_cache(hashing 3 필드) miss →
    file-manager 에 정말로 없는 상태. 호출자가 catch 후 file-manager 의
    `register_or_get` 으로 새 file_path 를 받아 stored_files 로 재요청하는 것이
    권장 복구 경로.
    """
```

---

### C4. fallback 의 두 번째 `get_file(new_path)` 가 보호되지 않음 → race 시 raw FMServiceError 전파

> check_cache hit 했어도 그 사이 새 file_path 의 파일이 다시 삭제될 수 있습니다 (race). 두 번째 get_file 이 404/5xx 면 FMServiceError 가 그대로 호출자로 전파되어 error_type='file_resolve' 가 아닌 다른 타입이 노출됩니다.

→ **수정**. [file_resolver.py:90-100](../service/app/logic/file_resolver.py#L90-L100) 두 번째 `get_file(new_path)` 를 `try/except FMServiceError` 로 감싸 `FileResolveError` 로 wrap. miss 와 race 가 동일하게 `error_type='file_resolve'` 로 통일됨 → caller 의 복구 경로 일관 유지.

회귀 테스트 2 개 추가 (race 시 404 / 5xx 양쪽): [test_file_resolver.py::TestFetchWithFallbackRace](../service/tests/logic/test_file_resolver.py).

---

### C5. SDK `__init__.py` 모듈 docstring 의 사용 예시가 옛 `files=[FileInput]`

> 이 PR diff 밖이지만 새 서버에서 즉시 422. 이 PR 에서 함께 갱신 권장.

→ **수정**. [sdk/llm_request/__init__.py:1-22](../sdk/llm_request/__init__.py#L1-L22) 모듈 docstring 을 `FileManagerHandler.register_or_get` + `StoredFile` + `stored_files=` 예시로 갱신.

before:
```python
사용법:
    from pathlib import Path
    import mimetypes
    from llm_request import llm_request_async, FileInput

    p = Path("report.pdf")
    file_input = FileInput(
        file_name=p.name,
        file_size=p.stat().st_size,
        file_mtime=int(p.stat().st_mtime),
        mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        extension=p.suffix.lstrip("."),
        data=p.read_bytes(),
    )
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        files=[file_input],
    )
```

after:
```python
사용법:
    from pathlib import Path
    from file_manager import FileManagerHandler
    from llm_request import llm_request_async, StoredFile

    fm = FileManagerHandler()
    p = Path("report.pdf")
    sf_dict = await fm.register_or_get(
        bytes_data=p.read_bytes(),
        metadata=fm.build_metadata(
            file_name=p.name,
            file_mtime=int(p.stat().st_mtime),
            file_size=p.stat().st_size,
        ),
    )
    response, meta = await llm_request_async(
        {"contents": "분석해줘", "model": "gemini-2.5-flash"},
        stored_files=[StoredFile(**sf_dict)],
    )
```

---

## Important

### I1. 비-404 raise 전 진단 로그가 한 줄도 없음

> 5xx / network err / timeout 으로 실패하면 그대로 raise — file-manager 다운 / 네트워크 단절을 운영자가 식별할 수 있는 신호가 없습니다.

→ **수정**. [file_resolver.py:71-75](../service/app/logic/file_resolver.py#L71-L75) raise 전 `logger.exception(...)` 추가.

before:
```python
    except FMServiceError as e:
        if e.status_code != 404:
            raise  # 비-404 (network / 5xx) 그대로 전파
```

after:
```python
    except FMServiceError as e:
        if e.status_code != 404:
            logger.exception(
                "file-manager get_file 실패 (비-404, 전파): file_path=%s, status=%s",
                fi.file_path, e.status_code,
            )
            raise  # 비-404 (network / 5xx) 그대로 전파
```

---

### I2. `StoredFile` extra="forbid" + 응답 dict 재사용 시 422 (입출력 shape 비대칭)

> 응답에는 resolved_via / 갱신된 file_path 등이 박혀서 내려가는데, agent 가 Phase 1 응답 dict 를 그대로 StoredFile(**dict) 로 다시 넣으면 422 가 납니다 (입력 6 필드 ↔ 응답 7+ 필드).

→ **README 안내 추가**. [README_v2.md "같은 파일 재호출" 섹션](../README_v2.md) 에 ⚠️ 박스 추가 — 응답 dict 그대로 `StoredFile(**...)` 시 422 발생, 6 필드 픽킹 one-liner 안내:
```python
ref = StoredFile(**{k: saved[k] for k in StoredFile.model_fields})
```

헬퍼 메서드 추가는 이번 PR 범위 밖.

---

### I3. PR 본문의 "옛 SDK silent drop" 표현은 사실 422 fail-fast — silent 아님

→ **정정**.
- [pull_request_v6.md](./pull_request_v6.md) 호환성 표 — "옛 Service" 를 "pre-Phase-2 Service (stored_files 필드 모름)" / "Phase 2 Service (stored_files 받지만 files 도 받음)" / "새 Service (Phase 3, files 거부)" 로 명시. silent drop 은 pre-Phase-2 한정 케이스.
- [debugging-guide.md](../debugging-guide.md) — phase 라벨 전부 제거하고 "stored_files 미지원 Service / 양쪽 받는 Service / 이번 PR Service" 로 사용자 관점 표기.

---

### I4. `test_requests_model.py:58` 폐기된 "upload" enum 테스트가 통과 — 회귀 보호 깨짐

→ **수정**. [test_requests_model.py:54-63](../service/tests/test_requests_model.py#L54-L63) 의 `("upload", "primary", "fallback")` → `("primary", "fallback")` 로 좁힘. 메서드명도 `test_accepts_upload_primary_fallback` → `test_accepts_primary_fallback`.

---

### I5. `[Phase3]` 로그 prefix ↔ debugging-guide.md 안내 `[Phase2]` 불일치

→ **수정**. [file_resolver.py:87](../service/app/logic/file_resolver.py#L87) 의 `[Phase3]` 로그 prefix 자체 제거. debugging-guide.md 안내도 통일.

before:
```python
logger.warning(
    "[Phase3] stale file_path: input=%s, resolved=%s, file_name=%s",
    fi.file_path, new_path, fi.file_name,
)
```

after:
```python
logger.warning(
    "stale file_path: input=%s, resolved=%s, file_name=%s",
    fi.file_path, new_path, fi.file_name,
)
```

---

### I6. `_estimate_non_file_bytes` 의 `except Exception → return 0` — 좁은 예외만

→ **별도 PR**. 이번 PR diff 밖 (기존 코드의 hardening). 작은 별도 PR 로 분리 — `except (TypeError, ValueError):` 로 좁힘.

---

### I7. cache hit dump 경로 Phase 1 시기 MDB doc 의 `resolved_via` 키 누락 보정

→ **README 안내 추가**. `resolved_via` 는 디버깅 용도 필드라 코드는 손 안 대고 (caller 가 `fm.get("resolved_via")` 로 안전 접근하면 됨), [README_v2.md "resolved_via 값" 섹션](../README_v2.md) 에 옛 doc 호환 안내 ⚠️ 박스 추가.

---

## Suggestions

### S1. README_v2.md 디렉터리 트리에 삭제된 `file_register.py` 잔존, 새 `file_resolver.py` 누락

→ **수정**. [README_v2.md:62](../README_v2.md#L62) 디렉터리 트리 갱신.

before:
```
│   │       ├── meta.py         # 저장용 메타 생성
│   │       ├── file_register.py       # file-manager에 파일 등록 (Service 진입 시점)
│   │       └── file_processing/
```

after:
```
│   │       ├── meta.py         # 저장용 메타 생성
│   │       ├── file_resolver.py       # stored_files → placeholder FileInput (lazy fetch)
│   │       └── file_processing/
```

---

### S2. `file_resolver.py` vs `file_processing/resolver.py` 이름 거의 동일 → 개명 권장

→ **별도 PR**. naming hygiene 은 별도 작업으로 분리.

---

### S3. `_upload_and_cache_file` / `_process_inline` 에서 `FileManagerHandler()` 매 호출 신규 인스턴스화 → 모듈 레벨 또는 DI

→ **반박**. [file_manager/handler.py:60-74](../../file-manager/sdk/file_manager/handler.py#L60-L74) 의 `FileManagerHandler` 는 `__new__` 기반 Singleton 으로 구현되어 있음. 첫 호출에서 `_instance` 에 저장하고 이후 호출은 같은 인스턴스 반환 + `__init__` 도 `_initialized` 플래그로 재진입 차단:

```python
class FileManagerHandler:
    _instance: Optional["FileManagerHandler"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._client = AsyncClient()
        self._initialized = True
```

→ `_upload_and_cache_file` / `_process_inline` 안에서 `FileManagerHandler()` 를 호출해도 매번 신규 객체를 만드는 게 아니라 이미 살아있는 싱글톤을 반환. `AsyncClient` (httpx) 도 첫 인스턴스 생성 시 한 번만 만들어지고 재사용. 모듈 레벨 변수로 끌어올리거나 DI 로 주입할 실익 없음 — 그대로 유지.

---

### S4. `models/requests.py` `StoredFile` docstring 의 "Phase 2" 라벨 → 영속적 문구로

→ **수정**. [requests.py](../service/app/models/requests.py) 의 `StoredFile` / `FileInput` docstring 에서 잘못된 Phase 1 / Phase 2 표기 정리. SDK 측 [sdk/models.py](../sdk/llm_request/models.py) 도 동기화. 코드의 Phase 3 (현재 동작 설명) 라벨은 유지.

**[StoredFile docstring]**

before:
```python
class StoredFile(BaseModel):
    """이미 file-manager 에 등록된 파일 참조 (Phase 2 입력).

    FileInput 의 5 메타 필드 + file_path 로 구성 — bytes 만 file_path 로 대체된 형태.
    Phase 1 ↔ Phase 2 caller mental model 대칭 (caller 는 Phase 1 응답의 6 필드를 그대로 다시 사용).
    ...
    extra="forbid": Phase 1 응답에 있는 server-fill 필드 (`resolved_via` 등) 를 caller 가
    실수로 함께 넘기면 ValidationError. ...
    """
```

after:
```python
class StoredFile(BaseModel):
    """이미 file-manager 에 등록된 파일 참조.

    FileInput 의 5 메타 필드 + file_path 로 구성 — bytes 만 file_path 로 대체된 형태.
    caller 는 file-manager `register_or_get` 결과 6 필드 dict 를 그대로 받아 StoredFile 로 변환.
    ...
    extra="forbid": 응답에 박힌 server-fill 필드 (`resolved_via` 등) 를 caller 가
    실수로 함께 넘기면 ValidationError. ...
    """
```

**[FileInput docstring 의 resolved_via 설명]**

before: `` `resolved_via` (Phase 2): bytes 가 어느 경로로 확보됐는지 추적``
after: `` `resolved_via`: bytes 가 어느 경로로 확보됐는지 추적``
