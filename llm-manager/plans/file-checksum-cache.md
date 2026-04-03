# File Checksum 기반 캐시 키 생성

## Context

현재 `contents`에 `FileRef`나 `Part`를 넣으면 파일 URI나 base64 데이터가 캐시 키에 포함된다.
- 같은 PDF를 재업로드하면 URI가 달라져 **캐시 미스** (비효율)
- 파일 내용이 바뀌어도 참조가 같으면 **잘못된 캐시 히트** (정합성 문제)

**목표**: 파일 **콘텐츠 해시(checksum)** 기반으로 캐시 키를 생성하여, 같은 파일은 히트 / 변경된 파일은 미스되도록 한다.

## 설계 요약

```
Client:
  llm_request_async(
      {"contents": ["요약해줘", file_ref]},
      file_paths=["report.pdf"],       # llm_input과 별도 파라미터
  )

Runner 내부 (_prepare):
  1. 검증: contents 내 FileRef/Part 개수 == file_paths 개수
  2. file_paths → checksum 계산 + 메타정보 추출 → file_meta 구성
  3. 캐시 키: checksum 값만 사용 (filename, size 등은 캐시 키에 미포함)
  4. MongoDB 저장: file_meta 전체 저장 (어떤 파일이 사용됐는지 추적)
```

핵심 원칙:
- `file_paths`는 llm_input **외부** 별도 파라미터 → llm_handler.call()에 전달 안 됨
- 캐시 키에는 **checksum만** 포함 (filename/size는 저장용)
- contents 내 FileRef/Part의 volatile 데이터(URI, base64)는 캐시 키에서 제거
- file_paths와 contents의 FileRef/Part 개수 불일치 시 **에러**
- file_paths 경로에 파일 없으면 **FileNotFoundError** (진행 안 됨)
- file_paths와 실제 FileRef가 같은 파일인지는 **검증 불가** → README에 주의사항 명시

---

## Step 1: `checksum.py` 신규 생성

**파일**: `llm_request/checksum.py`

내부 유틸리티 (public export 안 함).

```python
def file_checksum(source: str | Path, *, mime_type=None, filename=None) -> FileChecksum
```

- `FileChecksum` = TypedDict: `checksum`, `filename`, `size`, `mime_type`
- `checksum` 포맷: `"sha256:<64자 hex>"` (알고리즘 확장성)
- Python 3.11+ `hashlib.file_digest()` 활용 (대용량 파일 스트리밍 해시)
- MIME 타입 감지: `mimetypes` + `filetype` 라이브러리 (llm-handler에서 이미 사용하는 패턴)

---

## Step 2: `keying.py` 수정

**파일**: `llm_request/keying.py`

### 2a. `_LLM_INPUT_FIELDS`에 `"file_checksums"` 추가 (line 21)

`file_checksums`: checksum 문자열 리스트 (캐시 키용). `file_meta` 전체가 아님.

### 2b. `canonicalize_llm_input()`에서 FileRef/Part 정규화

```python
has_file_checksums = bool(llm_input.get("file_checksums"))
```

`_normalize()` 내부, `str(obj)` 폴백 직전에 추가:
- `file_checksums` 있을 때만 활성화 (없으면 기존 동작 유지 = 하위호환)
- `FileRef` → `{"_type": "file_ref", "mime_type": obj.mime_type}` (name, uri, state 제거)
- `Part(type="file")` → `{"_type": "file_part", "mime_type": obj.mime_type}` (file_uri 제거)
- `Part(type="inline")` → `{"_type": "inline_part", "mime_type": obj.mime_type}` (base64 data 제거)

import 추가: `from llm_handler.models import FileRef, Part`

---

## Step 3: `store.py` 수정

**파일**: `llm_request/store.py`

`save_result()` (line 56)에 `file_meta: list[dict] | None = None` 파라미터 추가.

```python
if file_meta:
    doc["file_meta"] = file_meta
```

doc 구성 시 `file_meta` 필드를 MongoDB 문서 최상위에 저장.

---

## Step 4: `runner.py` 수정

**파일**: `llm_request/runner.py`

### 4a. Public API 시그니처에 `file_paths` 파라미터 추가

`llm_request()`, `llm_request_async()`, `llm_request_stream()`, `llm_request_stream_async()` 모두:

```python
async def llm_request_async(
    llm_input: dict,
    *,
    file_paths: list[str] | None = None,   # 신규
    cache_mode: bool = True,
    save_mode: bool = True,
    ...
)
```

### 4b. `_prepare()`에서 file_paths 처리

`_prepare()`에도 `file_paths` 파라미터 추가.

```python
from .checksum import file_checksum as _compute_checksum

def _resolve_file_meta(llm_input: dict, file_paths: list[str] | None) -> list[dict] | None:
    """file_paths → file_meta 변환 + 개수 검증. 반환값은 저장용 file_meta."""
    contents = llm_input.get("contents")
    file_obj_count = 0
    if isinstance(contents, list):
        file_obj_count = sum(1 for c in contents if isinstance(c, (FileRef, Part)))

    if file_obj_count == 0:
        return None  # 파일 없으면 패스

    if not file_paths:
        raise ValueError(
            "contents에 FileRef/Part가 포함된 경우 file_paths를 반드시 제공해야 합니다."
        )

    if len(file_paths) != file_obj_count:
        raise ValueError(
            f"file_paths 개수({len(file_paths)})와 "
            f"contents 내 FileRef/Part 개수({file_obj_count})가 일치하지 않습니다."
        )

    return [_compute_checksum(p) for p in file_paths]
```

`_prepare()`에서:
1. `file_meta = _resolve_file_meta(llm_input, file_paths)` 호출
2. `file_meta`가 있으면 → checksum만 추출하여 keying용 llm_input 사본에 주입
3. `_Prepared` dataclass에 `file_meta` 필드 추가

```python
# keying용: checksum만 llm_input 사본에 주입
if file_meta:
    keying_input = {**llm_input, "file_checksums": [m["checksum"] for m in file_meta]}
else:
    keying_input = llm_input
dc = done_check(keying_input, ...)
```

### 4c. `_Prepared` dataclass에 `file_meta` 필드 추가

```python
@dataclass
class _Prepared:
    ...
    file_meta: list[dict] | None    # 신규
```

### 4d. `_finalize()` / `_finalize_stream()`에서 file_meta 전달

```python
file_meta=prep.file_meta,
```

---

## Step 5: `pyproject.toml` 의존성

**파일**: `pyproject.toml`

`filetype>=1.2.0`을 `dependencies`에 명시 추가 (현재 llm-handler 통해 전이적으로만 존재).

---

## Step 6: 테스트

### `tests/test_checksum.py` (신규)
- 파일 경로 입력 → 올바른 checksum, filename, size, mime_type
- 같은 내용의 다른 경로 → 동일 checksum
- filename/mime_type 오버라이드 동작
- checksum 포맷: `sha256:` + 64자 hex
- 존재하지 않는 경로 → FileNotFoundError

### `tests/test_keying.py` (수정)
- `file_checksums` 있을 때 FileRef → URI 없이 정규화됨
- `file_checksums` 있을 때 Part(inline) → base64 없이 정규화됨
- 같은 checksum + 다른 URI → 동일 캐시 키
- 다른 checksum → 다른 캐시 키
- `file_checksums` 없을 때 → 기존 동작 유지 (하위호환)

### `tests/test_store.py` (수정)
- `file_meta` 전달 시 문서에 포함됨
- `file_meta` 미전달 시 필드 없음

### `tests/test_runner.py` (신규 또는 수정)
- `file_paths` 전달 → file_meta 자동 생성 확인
- contents에 FileRef 있는데 `file_paths` 없으면 ValueError
- file_paths 개수 ≠ FileRef/Part 개수 → ValueError
- 텍스트만 → 검증 안 함

---

## Client 사용 예시

```python
from llm_request import llm_request_async, upload_file, Part

# ── FileRef + file_paths ──────────────────────────────────
file_ref = upload_file("report.pdf")
result, info = await llm_request_async(
    {"contents": ["이 PDF 요약해줘", file_ref]},
    file_paths=["report.pdf"],
)

# ── Part.from_file + file_paths ───────────────────────────
part = Part.from_file("report.pdf")
result, info = await llm_request_async(
    {"contents": ["이 PDF 요약해줘", part]},
    file_paths=["report.pdf"],
)

# ── 여러 파일 ─────────────────────────────────────────────
ref1 = upload_file("report1.pdf")
ref2 = upload_file("report2.pdf")
result, info = await llm_request_async(
    {"contents": ["비교 분석해줘", ref1, ref2]},
    file_paths=["report1.pdf", "report2.pdf"],
)

# ── 텍스트만 — file_paths 불필요 ──────────────────────────
result, info = await llm_request_async(
    {"contents": "오늘 날씨 어때?"},
)
```

---

## 주의사항 (README에 명시)

> **file_paths와 contents 파일 일치 책임**
>
> `file_paths`에 전달한 파일과 `contents`에 포함된 `FileRef`/`Part`가 실제로 같은 파일인지는
> llm_request에서 검증할 수 없습니다. 잘못된 경로를 전달하면 캐시 키가 잘못 생성되어
> 다른 파일에 대한 캐시 결과가 반환될 수 있습니다. **반드시 동일한 파일 경로를 사용하세요.**

---

## 수정 대상 파일 목록

| 파일 | 작업 |
|------|------|
| `llm_request/checksum.py` | **신규** - 내부 file_checksum 유틸리티 |
| `llm_request/keying.py` | 수정 - file_checksums 필드 추가, FileRef/Part 정규화 |
| `llm_request/store.py` | 수정 - file_meta 저장 |
| `llm_request/runner.py` | 수정 - file_paths 파라미터 + 검증 + file_meta 전달 |
| `pyproject.toml` | 수정 - filetype 의존성 |
| `tests/test_checksum.py` | **신규** - checksum 테스트 |
| `tests/test_keying.py` | 수정 - file_checksums 관련 테스트 |
| `tests/test_store.py` | 수정 - file_meta 저장 테스트 |
| `tests/test_runner.py` | 수정 - file_paths 검증 테스트 |

## 검증 방법

```bash
cd packages/llm-request
pytest tests/ -v
```
