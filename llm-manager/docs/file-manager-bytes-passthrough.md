# file-manager bytes passthrough

> **상태**: 구현 완료. 미결 항목은 §9 참조.

## 1. 배경 / 문제 정의

### 현재 구조 (개편 전)

```
에이전트 머신
  1. 메타 생성 (file_name, file_size, file_mtime)
  2. file_manager.acheck_cache(meta)
     └─ 미스면 file_manager.aupload_file(bytes, meta) → file_path 획득
  3. FileMeta 완성 (file_path 포함)
  4. llm_request(query, file_metas=[FileMeta, ...]) 호출
      ↓
llm-request 서비스
  5. resolver가 aget_file(meta.file_path)로 bytes 재획득
  6. LLM 호출 payload에 bytes 삽입
```

### 이 구조의 문제

1. **불필요한 왕복 I/O** — 에이전트가 이미 bytes를 갖고 있는데, 서비스가 file-manager에
   다시 요청한다. 매 요청마다 (file-manager ↔ llm-request) 왕복이 낀다.

2. **책임 분산** — 에이전트 개발자가 file-manager SDK까지 숙지해야 함. llm-request
   사용 진입 장벽을 높인다.

3. **저장 목적과 전달 목적의 혼동** — file-manager의 본래 목적은 **LLM 로깅용 파일 적재**
   (문제 발생 시 사후 조회). 응답 경로에 끼어들 이유가 없다.

### 변경 방향

- **에이전트의 책임 축소**: `(query, meta, bytes)`만 전달. file-manager를 몰라도 됨.
- **llm-request가 file-manager 호출을 흡수**: `check_cache`/`upload_file`을 서비스 진입점에서
  side-effect로 실행. 반환값의 meta(file_path 등)는 쓰지 않는다.
- **`aget_file` 제거**: 에이전트가 준 bytes를 그대로 LLM 호출에 사용. file-manager는
  **순수 로깅 저장소**로 역할 축소.

### 권한 경계

| 주체 | 제공/소유 |
|---|---|
| 에이전트 | `file_name`, `file_size`, `file_mtime`, `mime_type`, `extension`, `data(bytes)` |
| file-manager | `file_path` 등 내부 식별자 (UUID 기반, 외부로 노출하지 않음) |

hashing(cache key)에는 `file_name + file_size + file_mtime` 세 개만 사용.
나머지 meta는 LLM 호출 로직 및 로깅에 사용.

### 새 구조

```
에이전트 머신
  1. 메타 생성 (5개 meta 필드) + bytes 로드
  2. llm_request(query, files=[FileInput, ...]) 호출
      ↓
llm-request 서비스
  3. [진입] ingest_files: 각 파일마다
        acheck_cache(hash_meta) → 미스면 aupload_file(bytes, upload_meta)
  4. resolver는 에이전트가 준 FileInput을 그대로 받아 bytes + meta로 LLM payload 구성
  5. file-manager는 사후 로깅 조회용으로만 존재
```

### 명시적 비-목표 (non-goals)

- **파일 무결성 검증 (checksum 대조)** — 하지 않는다. 에이전트가 준 bytes와 file-manager에
  저장된 bytes가 달라질 경우 검출하지 않는다. 로깅 문제 발생 빈도가 낮은 점 + 결정사항에 따라.
- **mime_type / extension 내용 검증** — 에이전트가 제공한 값을 서비스는 검증 없이 신뢰.
  PDF를 `text/plain`으로 잘못 신고하면 그대로 LLM 호출에 사용됨. magic byte 기반 검증은 범위 밖.
- **LLM 응답 캐시 키의 file_checksum** — 영향 없음. 기존 `keying.make_file_checksum`
  로직(name+size+mtime 기반)은 그대로 유지.
- **Redis 파일 URI 캐시 / context cache** — 이 문서 범위 밖. 기존 구조 유지.

---

## 2. 사전 정보

### 2.1 file-manager SDK API

| 기능 | async 함수 | 동작 |
|---|---|---|
| 캐시 조회 | `acheck_cache(meta) -> bool` | meta: `{file_name, file_size, file_mtime}` → 존재 여부만 |
| 업로드 | `aupload_file(bytes, meta) -> None` | bytes + `{file_name, file_size, file_mtime, mime_type, extension}` 저장. 반환 없음 |
| 가져오기 | `aget_file(file_path) -> bytes` | 이번 변경에서 **사용 중단** (SDK엔 유지, llm-request에서 호출 안 함) |

**file-manager 패키지 동반 변경 완료**:
- 엔드포인트 `/files/check-cache` 응답이 `{"hit": bool}`로 축소
- `/files/upload` 응답이 `{"ok": true}`로 축소
- `UploadRequest`에 `mime_type`, `extension` 선택 필드 추가 (에이전트가 주면 서버가 그 값 저장,
  없으면 `file_name`에서 자동 추론 fallback)
- SDK `check_cache`/`acheck_cache` 반환이 `bool`, `upload_file`/`aupload_file` 반환이 `None`
- `CacheCheckResult` 타입 제거

### 2.2 async 선택 근거

llm-request는 FastAPI 기반 async 서비스. sync I/O 호출 시 이벤트 루프가 점유되어
그 시간 동안 모든 요청이 멈춘다. 따라서 file-manager 호출은 **async 버전 사용**.

상세: [concepts.md §비동기(async)와 이벤트 루프](concepts.md#비동기async와-이벤트-루프--sync-io가-전체를-멈추는-이유)

### 2.3 옵션 비교 (왕복 vs passthrough)

| 옵션 | 무결성 검증 | I/O 비용 | 선택 이유 |
|---|---|---|---|
| A. 왕복 (개편 전) | 서버 저장본으로 LLM 호출 → 전달본과 일치 보장 | 왕복 1회 | ✗ file-manager 버그/스토리지 오류는 이 구조도 못 잡음, 낭비 |
| B. Passthrough + checksum 대조 | 업로드 전후 SHA-256 비교 | 왕복 없음, hash 비용 | ✗ 방침상 불가 |
| C. Passthrough (검증 없음) | 없음 | 왕복 없음 | **✓ 채택 — 로깅 파일은 사후 조회 빈도 낮음** |

---

## 3. 스키마 설계

### 3.1 SDK 입력

기존:
```python
llm_request_async(
    llm_input,
    file_metas=[FileMeta, ...],  # 에이전트가 file-manager로 완성한 FileMeta
)
```

신규:
```python
llm_request_async(
    llm_input,
    files=[FileInput(file_name=..., file_size=..., file_mtime=...,
                     mime_type=..., extension=..., data=bytes), ...],
)
```

에이전트는 `FileInput`만 만든다. **`file_path`는 file-manager 내부 값이라 에이전트도 llm-request도
알 수 없고 다루지 않는다**.

### 3.2 HTTP 요청 DTO (`/v1/request`)

```python
class FileInput(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: bytes  # Pydantic이 JSON 직렬화 시 자동 base64 인코딩/디코딩

class LLMRequestBody(BaseModel):
    llm_input: dict
    files: list[FileInput] | None = None
    # 이하 기존 필드 유지 (cache_mode, save_mode, ttl_seconds, ...)
```

**인코딩 방식 — JSON + base64 고정**. Pydantic v2가 `bytes` 필드를 JSON 모드에서 자동으로
base64 처리해 주므로 수동 인코딩/디코딩 불필요.

(단점: base64로 크기 ~33% 증가. 대용량 파일 관련 처리는 §6 에러 처리에서 다룸.)

### 3.3 HTTP 요청 DTO (`/v1/done-check`)

done-check는 조회 전용이라 bytes가 불필요하다. hashing 3필드만 받는 mini DTO를 신설:

```python
class FileKeyingMeta(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]

class DoneCheckBody(BaseModel):
    llm_input: dict
    files: list[FileKeyingMeta] | None = None
    database: str | None = None
```

에이전트는 `os.stat` 한 번으로 3필드를 만들어 보내면 됨.

### 3.4 서버 내부 타입

별도 내부 타입 없음. **`FileInput`이 그대로 파이프라인에 흐른다** (meta 5필드 + bytes 모두 포함).
resolver, validators, keying 모두 FileInput을 직접 사용.

### 3.5 변경되지 않는 부분

- MDB 캐시 스키마 + `keying.make_file_checksum` 로직
- Redis 파일 URI 캐시 / context_cache 스키마
- file-manager 내부 저장소 구조 (file_path는 여전히 생성되지만 외부 비노출)

---

## 4. 전체 파이프라인

```
에이전트 머신
  ┌─────────────────────────────────────────────┐
  │ 1. 파일 로드 + 5개 meta 생성                 │
  │ 2. files=[FileInput(...)] (data 포함)        │
  │ 3. SDK llm_request_async(llm_input, files=)  │
  └──────────────────┬──────────────────────────┘
                     │ HTTP POST /v1/request
                     │ (JSON + base64)
                     ▼
llm-request Service (FastAPI, port 8101)
  ┌─────────────────────────────────────────────┐
  │ 4. 라우터: LLMRequestBody 수령              │
  │ 5. runner.llm_request_async() 호출           │
  └──────────────────┬──────────────────────────┘
                     ▼
  ┌─────────────────────────────────────────────┐
  │ 6. ingest_files(): 각 FileInput마다          │
  │    - acheck_cache(hash_meta) → bool          │
  │      └ 미스면 aupload_file(fi.data, 5필드)   │
  │    (side-effect만, 반환 meta 없음)           │
  │   이후 validate_mime_types/file_sizes        │
  └──────────────────┬──────────────────────────┘
                     ▼
  ┌─────────────────────────────────────────────┐
  │ 7. prepare(): cache key + MDB lookup        │
  │    (FileInput 그대로, make_file_checksum)    │
  └──────────────────┬──────────────────────────┘
                     │ cache miss
                     ▼
  ┌─────────────────────────────────────────────┐
  │ 8. resolve_files(files, ...)                 │
  │    - classify_files (FileInput 기반)         │
  │    - _process_inline: fi.data 사용           │
  │    - _process_files_api: fi.data 사용        │
  └──────────────────┬──────────────────────────┘
                     ▼
  ┌─────────────────────────────────────────────┐
  │ 9. LLM 호출 → finalize → MDB 저장           │
  └─────────────────────────────────────────────┘
```

---

## 5. 모듈별 상세

### 5.1 `sdk/llm_request/`

- **Public API (`__init__.py`)**: `llm_request_async(llm_input, files=[...])`.
- **기존 사이즈 기반 분기 (inline/Files API) 로직 삭제** — SDK는 사이즈를 보지 않고 파일을
  통째로 서버에 전송. 사이즈 판단은 서비스의 `resolver.classify_files`가 단일 진실.
- **`checksum.py` 부재** — cache key용 checksum은 서버의 `keying.make_file_checksum`에서 계산.
- **`FileInput`, `FileKeyingMeta`** re-export (`sdk/llm_request/models.py`).

### 5.2 `service/app/models/requests.py`

```python
class FileInput(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]
    mime_type: str
    extension: str
    data: bytes

class FileKeyingMeta(BaseModel):
    file_name: str
    file_size: int
    file_mtime: Union[str, int]

class LLMRequestBody(BaseModel):
    files: list[FileInput] | None = None
    # ... 이하 기존

class DoneCheckBody(BaseModel):
    files: list[FileKeyingMeta] | None = None
    # ... 이하 기존
```

### 5.3 `service/app/routers/request.py`

- `/v1/request`, `/v1/request/stream`: `body.files` 전달
- `/v1/done-check`: `body.files`의 `FileKeyingMeta` 리스트를 `make_file_checksum`에 전달
- 라우터는 DTO 전달만. 비즈니스 로직 없음.

### 5.4 `service/app/logic/ingest.py` (신규)

```python
async def ingest_files(inputs: list[FileInput]) -> list[FileInput]:
    """각 FileInput을 file-manager에 등록. 입력을 그대로 반환."""
    for fi in inputs:
        hash_meta = {
            "file_name": fi.file_name,
            "file_size": fi.file_size,
            "file_mtime": fi.file_mtime,
        }
        exists = await acheck_cache(hash_meta)
        if exists:
            continue
        upload_meta = {
            **hash_meta,
            "mime_type": fi.mime_type,
            "extension": fi.extension,
        }
        await aupload_file(fi.data, upload_meta)
    return inputs
```

side-effect 전용 (파일을 file-manager에 등록). llm-request는 file_path/FileMeta를 다루지 않음.

### 5.5 `service/app/logic/runner.py`

- `_Prepared.files: list[FileInput] | None` 필드
- `llm_request_async(files=...)` 진입에서 `ingest_files(files)` 호출 + validate
- `prepare`, `_build_server_call_input`, `_do_llm_call` 모두 `prep.files` 기반
- finalize에서 `[fi.model_dump(exclude={"data"}) for fi in prep.files]`로 MDB 감사 저장
  (bytes 제외)

### 5.6 `service/app/logic/file_handler/resolver.py`

`resolve_files(files: list[FileInput], ...)`로 시그니처 변경. 내부 함수들도 FileInput 직접 사용:

```python
async def _process_inline(inline_pool: list[FileInput]) -> list[Part]:
    for fi in inline_pool:
        inline_parts.append(Part.from_bytes(data=fi.data, mime_type=fi.mime_type))
        ...

async def _upload_and_cache_file(fi: FileInput, checksum, uri_parts, file_refs):
    # tempfile 생성 후 fi.data 기록 → Files API 업로드
    ...
```

### 5.7 validators, keying

- `validators.py`: 시그니처 `list[FileInput]`. `fi.mime_type`, `fi.file_name`, `fi.file_size` 직접 참조.
- `keying.make_file_checksum(meta: Any)`: `file_name + file_size + file_mtime` 속성만
  읽으므로 `FileInput`/`FileKeyingMeta` 모두 duck-typing으로 호환.

---

## 6. 에러 처리

### 6.1 file-manager 호출 실패

| 실패 지점 | 대응 |
|---|---|
| `acheck_cache` 예외 | 로그 + `CacheError` 래핑하여 상위로 — 요청 실패 |
| `aupload_file` 예외 | `FileUploadError` 래핑 — 요청 실패 |

대안 검토 — "file-manager 실패해도 bytes는 있으니 LLM 호출은 진행": **채택 안 함**.
file-manager 저장 실패는 로깅 감사 누락 위험으로 요청 자체를 실패 처리.

### 6.2 base64 디코드 실패

- Pydantic `bytes` 필드가 자동 디코딩하므로, 잘못된 base64는 `ValidationError`로 즉시 실패.
- FastAPI가 자동으로 HTTP 422 응답. 추가 핸들링 불필요.

### 6.3 메타와 실제 bytes 불일치 (file_size 다름)

- `len(fi.data) != fi.file_size`면 HTTP 400. 사이즈 비교는 metadata 범위 내라 허용.
- checksum/hash 비교는 **하지 않음** (방침).

### 6.4 대용량 파일 HTTP body 한도

- **현재 전제**: 파일 크기 ≤ 200MB 수준. JSON+base64 transport로 충분. 기본 FastAPI/uvicorn
  설정으로 추가 조정 없이 처리 가능.
- **향후 전환 트리거**: 500MB 이상 파일이 잦아지거나 동시 요청이 많아져 메모리가 문제되면
  multipart 전환 검토. 검토 시점이 되면 README 또는 별도 이슈로 기록해 두고 진행.

---

## 7. 로드맵

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 설계 문서 작성 | ✓ |
| 1 | file-manager SDK/서비스 단순화 (bool, None 반환) | ✓ |
| 2 | `models/requests.py` DTO 교체 (`FileInput`, `FileKeyingMeta`) | ✓ |
| 3 | `ingest.py` 신규 (`ingest_files`) | ✓ |
| 4 | `runner.py` — `_Prepared.files`, 진입부 `ingest_files` 호출 | ✓ |
| 5 | `resolver.py` — FileInput 기반 재작성 + `aget_file` 제거 | ✓ |
| 6 | 라우터 DTO 전달 조정 | ✓ |
| 7 | SDK 시그니처 교체 + 사이즈 분기 로직 삭제 | ✓ |
| 8 | validators 시그니처 교체, keying duck-typing | ✓ |
| 9 | 테스트 재작성 (`test_resolver_standby.py`) | ✓ |
| 10 | README.md / CLAUDE.md / 예시 노트북 업데이트 | 진행 예정 |

---

## 8. 사용 예시 (에이전트)

```python
from llm_request import llm_request_async, FileInput
from pathlib import Path
import mimetypes

p = Path("report.pdf")
file_input = FileInput(
    file_name=p.name,
    file_size=p.stat().st_size,
    file_mtime=int(p.stat().st_mtime),
    mime_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream",
    extension=p.suffix.lstrip("."),
    data=p.read_bytes(),  # Pydantic이 전송 시 자동 base64 인코딩
)

result, meta = await llm_request_async(
    llm_input={"model": "gemini-2.0-flash", "contents": "요약해줘"},
    files=[file_input],
)
```

- 에이전트는 **file-manager SDK를 import하지 않는다**.
- 로컬에서 bytes 읽어 SDK에 넘기면 끝. `file_path`는 서버가 file-manager 내부에서만 관리.

---

## 9. TODO 체크리스트

**타입/모델** — ✓
- [x] `service/app/logic/ingest.py` 신규 — `ingest_files()` (side-effect)
- [x] `service/app/models/requests.py` — `FileInput`, `FileKeyingMeta` 신규, `files` 필드 교체
- [x] `sdk/llm_request/models.py` — `FileInput`, `FileKeyingMeta` re-export

**서비스** — ✓
- [x] `service/app/routers/request.py` — `body.files` 전달로 교체
- [x] `service/app/logic/runner.py` — `_Prepared.files`, `ingest_files` 진입 호출
- [x] `service/app/logic/file_handler/resolver.py` — FileInput 기반 재작성

**SDK** — ✓
- [x] `sdk/llm_request/__init__.py` — `llm_request_async(files=...)` 시그니처
- [x] `sdk/llm_request/_client.py` — 송신 로직 (Pydantic JSON 직렬화)

**file-manager 패키지 (동반 변경)** — ✓
- [x] `packages/file-manager/service/app/routers/files.py` — 응답 JSON 축소, `UploadRequest`에 mime_type/extension 추가
- [x] `packages/file-manager/service/app/store.py` — `build_metadata` 파라미터 확장
- [x] `packages/file-manager/sdk/file_manager/client.py` — `check_cache`/`acheck_cache` → bool, `upload_file`/`aupload_file` → None
- [x] `packages/file-manager/sdk/file_manager/{__init__,models}.py` — `CacheCheckResult` 제거

**테스트** — ✓
- [x] `service/tests/logic/file_handler/test_resolver_standby.py` — FileInput 기반 재작성

**문서** — 진행 예정
- [ ] `README.md` — 사용 예시 신규 시그니처로 업데이트
- [ ] `CLAUDE.md` — 아키텍처 섹션, 흐름도 갱신
- [ ] `quickstarts/*.ipynb` — 신규 시그니처로 업데이트

**장래 과제 (지금 결정하지 않음)**
- [ ] 파일 크기 500MB 이상 트래픽이 생길 경우 JSON+base64 → multipart 전환 (§3.2, §6.4)
