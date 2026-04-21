# Pull Request #468 v3 — Review 대응 (블로커 A·B·C·D 해소)

# 📝 개요 (Overview)

PR #468 리뷰의 블로커 중 **A(Main과의 대규모 충돌)**, **B(Path Traversal)**, **C(Validation 순서 역전)**, **D(PDF splitter NamedTemporaryFile 처리 오류)** 를 해소한다.

A/B는 하나의 사건으로 요약된다 — **llm-request v0.2.0 개발 중 임시 플레이스홀더로 쓰던 `packages/file-manager/` stub을, file-manager 담당자가 main에 올린 공식 v1 구현으로 교체**. 그동안의 stub은 README와 설계 문서에 "임시 구현, 담당자 완성 시 대체" 로 명시되어 있었고, 이번 머지로 그 조건이 충족되어 계획대로 걷어냈다.

C/D는 이 교체와 무관한 **llm-request 코드 내부의 국소 버그 수정**. 각 수정은 기존 함수 동작을 바꾸지 않고 정상 경로의 시그니처도 건드리지 않는다.

- **Fix A** — stub 폐기 + v1 흡수 + llm-request 호출부의 최소 adaptation + stub 잔재(12개 파일) 정리
- **Fix B** — Fix A의 부수 효과로 취약 코드 경로(`store.py`) 자체가 저장소에서 사라짐. B-1 해소, B-2(file-handler 강화)는 후속 PR
- **Fix C** — `runner.py`에서 `register_files` 가 validate 앞에 있던 순서를 뒤집어, 미지원 MIME·2GB 초과 파일이 file-manager에 업로드되기 전에 fail-fast로 차단
- **Fix D** — `splitter.py`에서 `NamedTemporaryFile(delete=False)` 의 fd를 닫지 않은 채 `fitz.save` 를 호출하던 이중 핸들 패턴을 `tmp.close()` 한 줄로 교정


# 🔗 관련 문서

- [`pr_review_solve.md`](pr_review_solve.md) — 리뷰 체크리스트
- [`09.file-manager-separation.md`](09.file-manager-separation.md) — file-manager 외부 패키지 분리 설계. §3 "외부 의존 인터페이스 가정"에서 담당자와의 협의 스펙 정의
- [`10.file-manager-bytes-passthrough.md`](10.file-manager-bytes-passthrough.md) — llm-request 진입 시점의 `register_files` 책임 정의. stub 시절 API(`acheck_cache` / `aupload_file`)가 §2.1에 기록

# ✅ 해소 항목 (Fixes)

## Fix A. file-manager 임시 stub → 공식 v1 교체

### 배경

llm-request v0.2.0은 라이브러리에서 SDK + Service 2-tier로의 서버화를 수반한다. Service가 진입 시점에 file-manager에 파일을 등록(side-effect)하는 흐름이 설계에 포함되어 있어 (설계 문서 10.§1 "새 구조"), 개발을 진행하려면 file-manager 쪽 서비스가 필요했다.

그 시점에 **file-manager 담당자의 공식 구현은 아직 준비되지 않은 상태**였다. llm-request 측은 개발과 테스트를 계속 진행하기 위해 **최소한의 시연용 stub**을 `packages/file-manager/` 에 넣어두고 포트 8102로 써왔다. `README_v2.md`가 이 상태를 명시하고 있었다:

> `packages/file-manager`는 llm-request 개발·테스트용으로 간단히 구현한 임시 버전이다.
> 추후 file-manager 전담 담당자가 완성하면 해당 서비스로 대체·연결할 예정이다.

같은 문서의 "file-manager 실행" 섹션은 stub을 직접 띄우는 로컬 가이드도 제공하고 있었다:

```bash
cd packages/file-manager/service
uvicorn app.main:app --port 8102 --reload
```

4월 중순 이후 담당자가 file-manager v1을 main에 머지:
- `b6d1099 file manager ver1`
- `78c9761 feat(file-manager): file-handler 패키지 분리 및 SDK 메타데이터 추출 이관`
- `da4e0fe refactor(file-manager): config 기본값 중립화 및 미사용 코드 정리`

PR #468 리뷰 시점에는 PR 브랜치가 아직 stub(8102) 기준으로 돌아가고 있었고, main의 v1과 충돌(docker-compose.yml + `packages/file-manager/**` 7개 add/add)이 발생했다. 리뷰어는 "file-manager 구현 방침 결정이 최우선" 이라고 블로커 A로 지적.

### 결정

README에 예고된 대로 **stub은 폐기 시점이 온 것**이다. 따라서 다음 방향으로 수렴:

- **file-manager = main v1**만을 유일한 구현으로 채택. stub은 전부 제거.
- **llm-request의 file-manager 연동부**는 새 SDK(`FileManagerHandler`)에 맞춰 호출부만 교체.
- stub을 전제로 작성된 **문서·설정·테스트 파일**을 v1 기준으로 갱신하거나 삭제.

### 교체된 구성 요약 (stub → v1)

비교의 목적은 "두 설계 중 뭘 고를지" 가 아니라 "stub이 얼마나 간소했고 v1이 어떤 정식 구현으로 왔는가" 를 드러내는 것.

| 관점 | 기존 stub (feat/llm_request 한정) | v1 (담당자 구현, main) |
|---|---|---|
| 포트 | 8102 | **8600** |
| SDK API 형태 | 모듈 레벨 함수 2개 (`acheck_cache`, `aupload_file`) | **`FileManagerHandler` 싱글톤 클래스** |
| `check_cache` 반환 | `bool` | **`{"hit": bool, "file_path": str \| None}`** |
| `upload_file` 반환 | `None` (side-effect 전용) | **`str` (file_path)** |
| 파일 전송 | JSON + base64 | **multipart/form-data** |
| 스토리지 | 서비스 내부 `storage/` 디렉터리 | **`/data` 볼륨 마운트 + `file-handler` 패키지 위임** |
| 메타 DB | `kw_ai_dev` / `file_manager` | **`kw_ai` / `file_cache`** |
| URL prefix | `/files/…` | **`/v1/files/…`** |

### 해결 내용

#### 1. main 머지 — v1 흡수

- **`docker-compose.yml`** (content 충돌) — 수동 병합
  - file-manager 섹션: v1 기준 (포트 `8600`, `FILE_STORAGE_ROOT=/data`, `MDB_DATABASE` / `MDB_COLLECTION`, `${FILE_STORAGE_HOST_PATH:-/mnt/d/kw_ai}:/data` 볼륨)
  - llm-request 섹션: HEAD 버전 유지하되 `FILE_MANAGER_URL` 기본값만 `http://file-manager:8102` → `http://file-manager:8600` 으로 갱신
  - 주석: main의 운영/개발 override 가이드 버전 유지
- **`packages/file-manager/**`** 7개 add/add 충돌 — `git checkout --theirs`로 일괄 v1 채택
- 동반 유입: **`packages/file-handler/`** — v1이 스토리지 로직을 위임하는 신규 패키지. 리뷰 I-C3("file-handler 고아 여부")은 "v1이 사용 중이라 유지" 로 결론.

#### 2. llm-request 호출부 adaptation — `service/app/logic/file_register.py`

llm-request Service는 요청 진입 시점에 각 `FileInput`을 file-manager에 등록하는 side-effect를 수행한다(설계 문서 10.§1 "새 구조"). 이 책임 자체는 그대로이고, **stub SDK 호출을 v1 SDK 호출로 교체** 하기만 하면 된다.

```python
# Before — stub SDK
from file_manager import acheck_cache, aupload_file

async def register_files(inputs: list[FileInput]) -> list[FileInput]:
    for fi in inputs:
        hash_meta = {"file_name": fi.file_name,
                     "file_size": fi.file_size,
                     "file_mtime": fi.file_mtime}
        exists = await acheck_cache(hash_meta)         # bool
        if exists:
            continue
        upload_meta = {**hash_meta,
                       "mime_type": fi.mime_type,
                       "extension": fi.extension}
        await aupload_file(fi.data, upload_meta)       # None
    return inputs
```

```python
# After — v1 SDK
from file_manager import FileManagerHandler

async def register_files(inputs: list[FileInput]) -> list[FileInput]:
    fm = FileManagerHandler()                          # 싱글톤, 중복 인스턴스화 비용 없음
    for fi in inputs:
        hash_meta = {"file_name": fi.file_name,
                     "file_size": fi.file_size,
                     "file_mtime": fi.file_mtime}
        result = await fm.check_cache(hash_meta)       # dict
        if result["hit"]:
            continue
        upload_meta = {**hash_meta,
                       "mime_type": fi.mime_type,
                       "extension": fi.extension}
        await fm.upload_file(fi.data, upload_meta)     # str — 현재는 반환값 미활용
    return inputs
```

변경점 요약:
- **import**: 모듈 함수 2개 → 단일 클래스 1개
- **초기화**: 함수 본문에서 `FileManagerHandler()` 호출. v1 SDK가 싱글톤이라 매 요청마다 재호출해도 동일 인스턴스 재사용
- **`check_cache` 반환**: `bool exists` → `dict {"hit": bool, "file_path": str | None}`. 현재 로직은 `hit` 키만 사용
- **`upload_file` 반환**: `None` → `str file_path`. 현재 이 값은 버림. 추후 캐시 키 / 재조회 경로에서 활용 여지

#### 3. 포트 이관 (8102 → 8600) 및 연관 문서 갱신

- **`packages/llm-request/service/app/config.py`** — `file_manager_url` 기본값 `http://192.7.9.45:8102` → `http://192.7.9.45:8600`. "(포트는 TBD — 담당자 협의 후 확정)" 주석 제거 (담당자 확정됨)
- **`packages/llm-request/README_v2.md`**
  - 환경변수 표의 `FILE_MANAGER_URL` 기본값 업데이트 (`8102` → `8600`)
  - "## file-manager 실행" 섹션(stub 직접 띄우기 가이드) 전체 삭제. 이제 file-manager는 공식 이미지로 `docker compose --profile handlers up -d file-manager`로만 실행
- **`packages/llm-request/quickstarts/verify_all.ipynb`** — 서비스 표 / 헬스체크 / 환경값의 포트 `8102` → `8600`, Redis 실행 커맨드도 main 기준(`predixy` 7617)으로 갱신

#### 4. stub 잔재 정리 (main에 없고 HEAD에만 있던 12개 파일)

stub이 존재했던 시절의 파일이 브랜치에 남아있었다. `git ls-tree --name-only`로 main vs HEAD를 비교해 main에 없는 파일 12개를 식별 후 일괄 제거:

**stub 구현 코드·설정 (5개)**
- `packages/file-manager/pyproject.toml` (시연용 workspace root)
- `packages/file-manager/sdk/file_manager/client.py` (stub의 함수 기반 SDK 구현체)
- `packages/file-manager/sdk/file_manager/models.py` (stub SDK 모델)
- `packages/file-manager/service/app/store.py` (stub의 로컬 스토어 — **리뷰 B의 취약 파일**)
- `packages/file-manager/uv.lock`

**로컬 테스트 저장소에 커밋된 바이너리 (7개, 리뷰 F 범위)**
- `packages/file-manager/service/fixtures/sample.pdf`
- `packages/file-manager/service/storage/*.pdf` (4개)
- `packages/file-manager/service/storage/*.{txt,m4a,png}` (3개)

삭제 전 안전성 검증:
- `Grep "from file_manager\.client|from file_manager\.models"` → 참조 0건 (v1 SDK는 `_client.py` / `handler.py`를 사용)
- 워크스페이스 루트 `pyproject.toml` 부재 → 하위 stub pyproject 제거가 상위 설정에 영향 없음
- `store.py` → 라우터·v1 SDK 어디에서도 import되지 않는 dead code

이 스윕은 부수적으로 리뷰 항목 **B-1**(store.py 제거)과 **F**(바이너리 커밋 정리) 도 동시에 해소한다.

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 충돌 해결 | `docker-compose.yml` | file-manager(v1) + llm-request(포트만 갱신) 수동 병합 |
| 채택 (merge `--theirs`) | `packages/file-manager/**` (7개 충돌) | v1로 일괄 교체 |
| 신규 흡수 | `packages/file-handler/` | v1이 스토리지를 위임하는 동반 패키지 |
| 변경 | `packages/llm-request/service/app/logic/file_register.py` | SDK 호출부 stub → v1 |
| 변경 | `packages/llm-request/service/app/config.py` | `file_manager_url` 기본값 8600 |
| 변경 | `packages/llm-request/README_v2.md` | 포트 갱신 + "임시 실행" 섹션 제거 |
| 변경 | `packages/llm-request/quickstarts/verify_all.ipynb` | 서비스 표 포트/URL 갱신 |
| 삭제 | `packages/file-manager/pyproject.toml` | stub workspace root |
| 삭제 | `packages/file-manager/sdk/file_manager/{client,models}.py` | stub SDK |
| 삭제 | `packages/file-manager/service/app/store.py` | stub 로컬 스토어 (B 취약 파일) |
| 삭제 | `packages/file-manager/uv.lock` | stub lock |
| 삭제 | `packages/file-manager/service/{fixtures,storage}/*` | stub 테스트 바이너리 8개 |

### 사용자 관점 변화

- **llm-request SDK 외부 사용자(에이전트)**: 브레이킹 없음. `llm_request_async(..., files=[FileInput(...)])` 시그니처 동일.
- **운영 관점**: file-manager 실행 방식이 변경됨.

```bash
# Before — llm-request 개발자가 stub을 직접 실행
cd packages/file-manager/service
uvicorn app.main:app --port 8102 --reload

# After — 공식 file-manager 서비스로 실행
docker compose --profile handlers up -d file-manager   # port 8600
```

### 영향 범위

- **Breaking 없음** — llm-request SDK 공개 API 동일
- **DB/컬렉션 일원화** — `kw_ai` / `file_cache` 한 쌍만 사용. stub 시절의 `kw_ai_dev` / `file_manager` 조합 제거
- **포트 일원화** — file-manager 8600. 외부 코드에서 `8102` 하드코딩이 있다면 업데이트 필요 (현 레포 내부에는 없음 — grep 확인)
- **바이너리 저장소 비움** — `packages/file-manager/service/{storage,fixtures}/` 비워짐. `.gitignore`에 `packages/*/service/storage/`, `packages/*/service/fixtures/` 추가 권장 (리뷰 F 재발 방지)
- **`packages/file-handler/` 신규 의존** — v1 file-manager가 내부적으로 사용

### 관련 리뷰 항목

- 🚨 **A** (main 충돌) 완전 해소
- 🚨 **B-1** (store.py 제거) 해소 — Fix A의 stub 잔재 정리에 포함
- 🚨 **F** (바이너리 커밋) 해소 — Fix A의 stub 잔재 정리에 포함
- ⚠️ **I-C2** (docker-compose DB/컬렉션 이름 정합) 해소 — v1 단일 규격으로 통일
- ⚠️ **I-C3** (file-handler 고아 여부) 확인 — v1이 사용 중, 유지
- ⚠️ **I-C4** (kiwoom-file-manager 버전 역행) 해소 — main 1.0.0 채택

---

## Fix B. Path Traversal 취약점 해소 (stub 제거의 부수 효과)

### 배경

리뷰 원 지적: stub 시절 `packages/file-manager/service/app/store.py:84`의 `get_file_bytes`가 클라이언트가 보낸 `file_path`를 경계 검사 없이 결합.

```python
# 취약 코드 (stub)
def get_file_bytes(file_path: str) -> bytes:
    full_path = Path(settings.storage_dir) / file_path
    if not full_path.is_file():
        raise FileNotFoundError(f"file not found: {file_path}")
    return full_path.read_bytes()
```

`../../../etc/passwd` 같은 입력으로 `storage_dir` 밖의 임의 파일을 읽을 수 있는 구조.

### 해결 원리 — 실행 경로가 사라짐 + 동일 기능이 안전하게 재구현

Fix A의 stub 폐기로 **취약 코드가 있던 경로 자체가 저장소에서 제거**된다. v1이 file-manager 역할을 맡으면서 파일 I/O는 `file-handler` 패키지에 위임되고, 해당 패키지는 경계 검사를 이미 내장하고 있다.

v1 라우터(`packages/file-manager/service/app/routers/files.py:23, 156`):
```python
from file_handler import read_file, save_file
# ...
file_data = await read_file(file_path)
```

`file-handler` 경계 검사 (`packages/file-handler/file_handler/storage.py:32-38`):
```python
def _resolve_safe_path(file_path: str) -> Path:
    """저장소 루트 내부의 경로인지 검증 후 반환. 경로 조작 방지."""
    root = _get_storage_root().resolve()
    full_path = (root / file_path).resolve()
    if not str(full_path).startswith(str(root)):
        raise FileStorageError(f"잘못된 파일 경로: {file_path}")
    return full_path
```

`save_file` · `read_file` 모두 이 헬퍼를 경유. 결과적으로 "취약 코드는 더 이상 존재하지 않고, 동일 목적의 연산은 안전하게 수행되는" 상태.

### store.py 자체 제거 (B-1)

Fix A의 stub 잔재 정리 스윕(§해결 내용 #4)에 포함. 취약 파일이 트리에서 완전히 사라지므로 정적 분석 · 리뷰에서도 더 이상 이름이 검색되지 않는다.

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 삭제 | `packages/file-manager/service/app/store.py` | Fix A의 stub 잔재 정리에 포함 |

> 이 섹션의 목적은 "왜 B가 해소되었는가" 를 명시적으로 기록하는 것. 실제 변경 작업은 Fix A와 공유.

### 관련 리뷰 항목

- 🚨 **B** (Path Traversal) 해소 — 취약 경로 제거 + 안전한 대체 경로 존재 확인
- 🚨 **B-1** (store.py 제거) 해소

---

## Fix C. Validation 순서 역전 — fail-fast로 교정

### 배경

리뷰 원 지적: `packages/llm-request/service/app/logic/runner.py` 의 `llm_request_async`(438-441)와 `llm_request_stream_async`(562-565) 두 블록에서 파일 검증 3단계의 순서가 **`register_files → validate_mime → validate_size`** 로 역전되어 있다. 설계 의도는 **`validate_mime → validate_size → register_files`** 순.

수정 전 실제 코드:

```python
if files:
    await register_files(files)      # 먼저 file-manager에 업로드
    validate_mime_types(files)        # 그 뒤에 MIME 검증
    validate_file_sizes(files)        # 그 뒤에 크기 검증
```

이 순서대로면:
- **2GB 초과 파일**도 일단 file-manager로 업로드된 뒤 422 반환
- **미지원 MIME**(화이트리스트 밖) 파일도 일단 업로드된 뒤 422 반환

업로드 한 번에 드는 비용 = HTTP 대역폭 + file-manager 쪽 디스크 쓰기 + MongoDB metadata insert. 정상 경로에선 필요하지만, 어차피 거절될 파일에 대해선 전부 낭비. 특히 "2GB 파일을 네트워크로 다 받아놓고 나서 크기 초과라고 거절" 하는 최악 시나리오.

더구나 `validators.py` 자체 docstring이 이미 의도를 명시하고 있었다:

> runner의 llm_request_async / llm_request_stream_async 진입 직후 fail-fast로 잘못된 입력을 차단한다 (prepare·MDB 조회 이전).

즉 설계 문서·모듈 docstring이 "validate 먼저" 를 말하고 있는데 구현이 반대로 되어 있던 상태. 리뷰어 주장 100% 타당.

### 검증 — 세 함수의 독립성

순서만 뒤집는 **최소 diff** 로 충분한지 확인하기 위해 세 함수의 데이터 의존성을 점검:

| 함수 | 참조하는 필드 | 반환값 | register 결과에 의존? |
|---|---|---|---|
| `validate_mime_types` | `fi.mime_type` (화이트리스트 대조) | `None` (raise on fail) | X |
| `validate_file_sizes` | `fi.file_size` (2GB 상한 비교) | `None` (raise on fail) | X |
| `register_files` | FileInput 전체 (side-effect) | 입력 리스트 그대로 반환 | — |

세 함수 모두 **`FileInput` 의 기존 속성만** 읽는다. `register_files` 는 side-effect(file-manager 업로드)만 수행하고 입력을 변경 없이 반환(`file_register.py:67`). 따라서 순서만 뒤집으면 로직 재설계 없이 해소된다.

### 해결 내용

두 블록 모두 동일 패턴으로 교정:

```python
# Before
if files:
    await register_files(files)
    validate_mime_types(files)
    validate_file_sizes(files)
```

```python
# After
if files:
    validate_mime_types(files)
    validate_file_sizes(files)
    await register_files(files)
```

두 블록의 수정 전 패턴이 완전히 동일해서 `replace_all` 한 번으로 교정 완료. 함수 경계 밖에는 아무 변경 없음.

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/runner.py` | 두 블록(`:438-441`, `:562-565`) 순서 교정 |

### 영향 범위

- **Breaking 없음** — 외부 시그니처·정상 경로 동작 동일. MIME 통과 + 2GB 이하 파일의 처리 흐름은 그대로
- **실패 경로만 개선** — 미지원 MIME · 2GB 초과 시 file-manager 호출 없이 422 즉시 반환. 네트워크/디스크/MDB 낭비 제거
- **테스트**: `service/tests/logic/test_runner_standby.py` 는 `files` 파라미터 미사용 + validate/register 호출 자체를 assert하지 않음 → 이번 변경에 영향 없음. `validators.py` · `file_register.py` 전용 단위 테스트는 아직 없음(리뷰 I-Q11 범위, 후속 처리 예정)

### 관련 리뷰 항목

- 🚨 **C** (Validation 순서 역전) 해소

---

## Fix D. splitter NamedTemporaryFile 이중 핸들 — `tmp.close()` 추가

### 배경

리뷰 원 지적: `packages/llm-request/service/app/logic/file_processing/splitter.py` 의 두 위치 (`:122-123` 분할 파트 저장, `:161-162` 병합 파트 저장)에서 `NamedTemporaryFile(delete=False)` 직후 **`close()` 없이** 같은 경로에 `fitz.save()` 를 호출.

수정 전 실제 코드:

```python
tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
# ← 이 시점에 tmp가 파일을 열고 있는 fd 보유 (빈 파일)
part_doc.save(tmp.name, garbage=4, deflate=True)
# ← fitz가 같은 경로를 또 열어 쓰려 함 → 이중 핸들
```

### OS별 동작 차이

| OS | 같은 파일 이중 열기 허용 | 결과 |
|---|---|---|
| **Windows** | 기본 공유 모드 엄격 (다른 핸들 차단) | `fitz.save` open 실패 → `PermissionError` 확정 |
| **Linux / macOS** | 허용 (여러 fd가 같은 inode 가리킴) | 대부분 성공. 단 버퍼 flush 타이밍에 따라 바이트 꼬임 가능성 존재 (tempfile이 실제로 쓰지 않으면 거의 문제 없음) |

운영 컨테이너가 Linux라 "실제로는 잘 돌았다" 가 성립했지만, 로컬 개발 환경이 Windows일 경우 `split_pdf` 가 `PermissionError` 로 터진다. 이식성 · 이론적 정합성 측면에서 고쳐야 하는 버그.

### 검증 — 동시성 문제 아님

혼동하기 쉬운 부분을 명시적으로 정리 (상세: `docs/00.concepts.md` §"NamedTemporaryFile과 이중 핸들 문제"):

- **D는 단일 호출 내부 문제.** 한 번의 `split_pdf` 호출 안에서 `tmp` 의 fd와 `fitz.save` 가 여는 fd가 공존하는 상태가 원인
- **비동기·동시성과 무관.** `tempfile` 은 호출마다 고유 경로를 할당하므로 여러 요청이 병렬로 분할해도 서로 다른 파일을 쓴다. 요청 간 경쟁 아님
- **함수 return 시 GC로 닫히긴 함**, 하지만 그건 `save` 이후 얘기. 문제는 `save` 호출 *시점* 의 이중 핸들

### 해결 패턴 비교

세 옵션을 검토한 후 옵션 1 채택:

```python
# 옵션 1: tmp.close() 한 줄 추가 — 채택
tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
tmp.close()
part_doc.save(tmp.name, garbage=4, deflate=True)

# 옵션 2: mkstemp + os.close
fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
os.close(fd)
part_doc.save(tmp_name, garbage=4, deflate=True)

# 옵션 3: with 문으로 fd 범위 제한
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
    tmp_name = tmp.name
part_doc.save(tmp_name, garbage=4, deflate=True)
```

옵션 1 채택 사유: **최소 diff**. 기존 `tmp = ...; ... ; finally 블록에서 tmp.name 참조` 구조를 건드리지 않음. 옵션 2/3은 스타일 변경이 커서 `try/finally` 블록 배치까지 재구성해야 함. `delete=False` 라는 기존 계약(파일 수명은 호출자가 관리)도 유지됨.

### 해결 내용

두 블록 모두 `NamedTemporaryFile(...)` 직후 `tmp.close()` 한 줄 삽입.

```diff
             part_doc.select(group)
             tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) # 분할 pdf 임시 생성
+            tmp.close()
             part_doc.save(tmp.name, garbage=4, deflate=True)
         finally:
             part_doc.close()
```

```diff
                     tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
+                    tmp.close()
                     doc_m.save(tmp.name, garbage=4, deflate=True)
                 finally:
                     doc_m.close()
```

### 부작용 검토

- `delete=False` → `close()` 해도 파일이 디스크에 남음. 이후 `save` 가 같은 경로에 덮어쓰기 → 정상 동작
- 기존 `os.unlink(tmp.name)` / `os.unlink(part_path)` 정리 로직은 변함 없이 동작
- `tmp` 객체 GC 시점의 재close 시도는 파이썬의 `close` 가 idempotent → 조용히 no-op
- `uploader.py` 등 tmp.name을 업로드 후 삭제하는 흐름도 영향 없음

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/file_processing/splitter.py` | 두 곳(`:123`, `:163`)에 `tmp.close()` 추가 |
| 문서 | `packages/llm-request/docs/00.concepts.md` | "NamedTemporaryFile과 이중 핸들 문제" 섹션 추가 |

### 영향 범위

- **Breaking 없음** — Linux에서도 수정 전후 동작이 동일 (이전엔 "우연히 통과", 이후엔 "원리상 안전하게 통과")
- **Windows 이식성 확보** — `split_pdf` 가 더 이상 Windows에서 `PermissionError` 로 터지지 않음
- **이론적 리스크 제거** — Linux에서의 잠재적 버퍼 타이밍 이슈도 원천 봉쇄

### 관련 리뷰 항목

- 🚨 **D** (PDF splitter NamedTemporaryFile) 해소
- 💡 **S4** (`resolver.py:163-170` `NamedTemporaryFile(delete=False)` → `with` 권장) — 해당 위치는 이미 `tmp.close()` 후 `upload_*` 를 호출하는 패턴이라 D와 같은 버그가 아님. 순수 스타일 제안이므로 이번 PR 범위 밖(후속 follow-up)으로 분리

---

# 📌 참고 사항

- `ec3eb6d Merge remote-tracking branch 'origin' into feat/llm_request` — main origin의 winus-handler 리팩토링이 함께 유입된 부수 머지. llm-request 로직과 무관.
- `156c02f test` — `verify_all.ipynb` 재실행 결과(출력 셀 포함) 커밋. 기능 변경 없음.
