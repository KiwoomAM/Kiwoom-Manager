# Pull Request #468 v3 — 리뷰 전면 대응 및 후속 정리

# 📝 개요 (Overview)

PR #468 리뷰 전반에 대응하고, 그 과정에서 자연스럽게 드러난 3가지 주변 이슈(orchestrator 폐기, file-manager `file_path` 보존, docker env 명칭 정합)까지 같이 정리한다.

해소 항목 요약:
- **블로커 6** — A(Main 충돌), B(Path Traversal), C(Validation 순서), D(splitter tmp), E(SDK 의존성), F(바이너리 커밋)
- **아키텍처 2** — I-A2(스트림 반환 타입), I-A6(서버 종료 시 close). I-A1/I-A3 는 orchestrator 제거로 자동 해소
- **코드 품질 6** — I-Q3(close 공개), I-Q4(async Redis 래핑), I-Q5(BaseJsonStore 캡슐화), I-Q6(예외 로깅), I-Q7(model None 검증), I-Q11(테스트 29개 추가). I-Q9 는 orchestrator 제거로 자동 해소
- **호환성 1** — I-C1(이주 안내). I-C2/C3/C4 는 Fix A 에 자동 흡수
- **제안 2** — S2(설계 문서 이름 정합), S3(`context_cache_ttl=0` 명시)

수정 불필요로 결론 낸 항목 (§📌 참고 사항 에 근거 기록): I-Q1(CLAUDE.md 금지 근거 없음), I-Q8(Base64Field 중복 의도적), I-A4(re-export 의도적), I-A5(collection 하드코딩 유지), S1(IP fallback 유지), S4(`delete=True` 는 오히려 후퇴), B-2(사내망 + 내부 UUID 라 위협 모델 성립 안 함), F-2(`.gitignore` 보강은 레포 루트 수정이라 범위 밖).

A/B/F 는 하나의 사건으로 요약된다 — **llm-request v0.2.0 개발 중 임시 플레이스홀더로 쓰던 `packages/file-manager/` stub 을, file-manager 담당자가 main 에 올린 공식 v1 구현으로 교체**. README 와 설계 문서에 "임시 구현, 담당자 완성 시 대체" 로 명시돼 있던 조건이 충족되어 걷어냈다.

C/D/E/I-Q*/I-A6 는 이 교체와 무관한 **llm-request 코드·패키지 메타데이터의 국소 개선** — 기존 정상 경로 동작·시그니처에 영향 없음. **I-A2(Fix G) 만 SDK 스트림 호출 패턴을 바꾸는 유일한 breaking change** 로, Kiwoom-Rag 전역 grep 결과 외부 호출자 0건을 확인한 후 진행. I-Q11(Fix L) 은 신규 테스트 29개 추가로 위 변경들의 회귀 방지 기반 마련.

## Fixes — 리뷰 원문 대응

- **Fix A** — stub 폐기 + v1 흡수 + llm-request 호출부의 최소 adaptation + stub 잔재(12개 파일) 정리
- **Fix B** — Fix A의 부수 효과로 취약 코드 경로(`store.py`) 자체가 저장소에서 사라짐. B-1 해소. B-2(file-handler 경계 검사 강화)는 **드롭** — 사내망 전용 배포 + `file_path` 가 file-manager 내부 UUID 라 외부 공격자 통제 불가, 위협 모델 성립 안 함
- **Fix C** — `runner.py`에서 `register_files` 가 validate 앞에 있던 순서를 뒤집어, 미지원 MIME·2GB 초과 파일이 file-manager에 업로드되기 전에 fail-fast로 차단
- **Fix D** — `splitter.py`에서 `NamedTemporaryFile(delete=False)` 의 fd를 닫지 않은 채 `fitz.save` 를 호출하던 이중 핸들 패턴을 `tmp.close()` 한 줄로 교정
- **Fix E** — `sdk/pyproject.toml` 의존성 선언 정리. (1) 누락된 `kiwoom-llm-handler` 추가 — 워크스페이스 경로(`[tool.uv.sources]`)는 이미 설정돼 있어 루트 `uv sync` 환경에선 우연히 돌았지만 sdk 단독 설치·배포 시 ImportError. (2) sdk 코드에서 import 0건인 dead dep 3개(`kiwoom-redis-handler`, `kiwoom-file-manager`, `filetype`) 제거
- **Fix F** — `packages/file-manager/service/{storage,fixtures}/` 의 바이너리 파일들은 **Fix A의 stub 잔재 정리에 이미 포함되어 제거됨** (별도 섹션 없음). 재발 방지용 `.gitignore` 보강은 follow-up
- **Fix G** — `llm_request_stream_async` · `llm_request_stream` 반환 타입을 `tuple[AsyncIterator, dict]` 로 변경 (I-A2). 스트림 호출자가 `stream, meta = await ...` 패턴으로 `cache_hit` 등 meta 를 시작 전 확인 가능. HTTP 레이어는 `X-LLM-Meta` 응답 헤더로 전달. SDK 호출 패턴 변경 (breaking) — Kiwoom-Rag 내 외부 호출자 0건 확인 후 진행
- **Fix H** — FastAPI `lifespan` shutdown 에 `FileManagerHandler().close()` 추가 (I-A6). 리뷰어의 "매 호출마다 AsyncClient 생성" 주장은 실제와 다름 — `FileManagerHandler` 는 이미 `__new__` 기반 싱글톤으로 커넥션 풀 재사용 중. 누락됐던 close 만 보강
- **Fix I** — `dedup.py` 3곳의 sync Redis 호출을 `await asyncio.to_thread(...)` 로 래핑 (I-Q4). 이벤트 루프 블로킹 제거. `redis-handler` async 전환은 범위 밖이라 호출자 책임 범위에서만 비동기 친화화
- **Fix J** — `cache_store.py` 의 모듈 함수 6개를 `FileCacheStore(BaseJsonStore)` 클래스 메서드로 이동 (I-Q5). `BaseJsonStore` 의 protected 메서드를 외부에서 호출하지 않도록 캡슐화 회복. 모듈 함수는 호출자 호환성을 위해 위임 형태로 유지
- **Fix K** — `context_cache` 생성 분기에서 `model is None` 일 때 명시적 `ValueError` raise (I-Q7). 기존 타입 흐름이 좁아지면서 모호한 Gemini API 에러로 터지던 경로를 fail-fast 한국어 에러로 대체
- **Fix L** — 신규 단위 테스트 파일 3개 / 총 29 tests 추가 (I-Q11). `test_validators.py` · `test_resolver.py` · `test_file_register.py` — 본 PR 의 Fix C/D/I/J/K 회귀 방지
- **Fix M** — SDK `async def close()` 공개 함수 추가 + `README.md` 안내 (I-Q3). agent 프로세스 종료 시 `await close()` 호출로 httpx 커넥션 풀 정리
- **Fix N** — `_estimate_non_file_bytes` 예외 블록에 `logger.exception` 추가 (I-Q6). silent failure 해소. fallback 값(`return 0`) 자체는 유지 — 실패 케이스 자체가 극히 드문 edge 라 fallback 변경의 실익 미미

## 리뷰 외 추가 정리

- **orchestrator 제거** — 라이브러리 시절 `run_many` / `run_many_stream` 의 구현체 폐기. SDK·Service 런타임에서 참조 0건 확인 후 삭제. I-A1 / I-A3 / I-Q9 자동 해소
- **`file_path` MDB `file_meta` 보존** — Fix A 의 v1 SDK 반환값(`file_path: str`) 을 버리던 상태에서, `FileInput` 에 필드 추가 + file-manager 등록 시 저장. 후속 흐름(캐시 hit 결과의 원본 파일 추적 등)에서 별도 매핑 없이 사용 가능
- **docker env `MDB_*` → `MONGODB_*` 통일** — `file-manager` 만 prefix 가 달라 있던 걸 다수파 `MONGODB_*` 로 통일. 운영자 `.env` override 키 이름만 변경, 런타임 동작·경로·데이터 위치 모두 동일


# 🔗 관련 문서

- [`09.file-manager-separation.md`](09.file-manager-separation.md) — file-manager 외부 패키지 분리 설계. §3 "외부 의존 인터페이스 가정"에서 담당자와의 협의 스펙 정의
- [`10.file-manager-bytes-passthrough.md`](10.file-manager-bytes-passthrough.md) — llm-request 진입 시점의 `register_files` 책임 정의. stub 시절 API(`acheck_cache` / `aupload_file`)가 §2.1에 기록

# ✅ 해소 항목 (Fixes)

## Fix A. file-manager 임시 stub → 공식 v1 교체

### 배경

llm-request v0.2.0은 라이브러리에서 SDK + Service 2-tier로의 서버화를 수반한다. Service가 진입 시점에 file-manager에 파일을 등록(side-effect)하는 흐름이 설계에 포함되어 있어 (설계 문서 10.§1 "새 구조"), 개발을 진행하려면 file-manager 쪽 서비스가 필요했다.

그 시점에 **file-manager 담당자의 공식 구현은 아직 준비되지 않은 상태**였다. llm-request 측은 개발과 테스트를 계속 진행하기 위해 **최소한의 시연용 stub**을 `packages/file-manager/` 에 넣어두고 포트 8102로 써왔다. `README.md`가 이 상태를 명시하고 있었다:

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
        await fm.upload_file(fi.data, upload_meta)     # str — file_path. 본 PR 추가 변경에서 활용 시작 (§➕ 참고)
    return inputs
```

변경점 요약:
- **import**: 모듈 함수 2개 → 단일 클래스 1개
- **초기화**: 함수 본문에서 `FileManagerHandler()` 호출. v1 SDK가 싱글톤이라 매 요청마다 재호출해도 동일 인스턴스 재사용
- **`check_cache` 반환**: `bool exists` → `dict {"hit": bool, "file_path": str | None}`. 본 PR 추가 변경에서 `file_path`까지 활용 (§➕)
- **`upload_file` 반환**: `None` → `str file_path`. 본 PR 추가 변경에서 MDB `file_meta`에 보존 (§➕)

#### 3. 포트 이관 (8102 → 8600) 및 연관 문서 갱신

- **`packages/llm-request/service/app/config.py`** — `file_manager_url` 기본값 `http://192.7.9.45:8102` → `http://192.7.9.45:8600`. "(포트는 TBD — 담당자 협의 후 확정)" 주석 제거 (담당자 확정됨)
- **`packages/llm-request/README.md`**
  - 환경변수 표의 `FILE_MANAGER_URL` 기본값 업데이트 (`8102` → `8600`)
  - "## file-manager 실행" 섹션(stub 직접 띄우기 가이드) 전체 삭제. 이제 file-manager 실행 방법은 **공식 이미지가 표준** (`docker compose --profile handlers up -d file-manager`). 로컬 개발 시엔 `cd packages/file-manager/service && uv run uvicorn app.main:app --port 8600` 로 직접 실행도 가능 (`verify_all.ipynb` 가 이 방식 사용)
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
| 변경 | `packages/llm-request/README.md` | 포트 갱신 + "file-manager 실행" 섹션 제거 |
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
- 🚨 **B-2** (file-handler `startswith` → `is_relative_to` 강화) **드롭** — 사내망 전용 배포 + `file_path` 가 file-manager 내부 UUID 라 외부 공격자 통제 불가, 위협 모델 성립 안 함. 이슈 raise 도 생략

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
- **테스트**: `service/tests/logic/test_runner_standby.py` 는 `files` 파라미터 미사용 + validate/register 호출 자체를 assert하지 않음 → 이번 변경에 영향 없음. `validators.py` · `file_register.py` 전용 단위 테스트는 **Fix L 에서 추가됨** (리뷰 I-Q11)

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

혼동하기 쉬운 부분을 명시적으로 정리:

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

### 영향 범위

- **Breaking 없음** — Linux에서도 수정 전후 동작이 동일 (이전엔 "우연히 통과", 이후엔 "원리상 안전하게 통과")
- **Windows 이식성 확보** — `split_pdf` 가 더 이상 Windows에서 `PermissionError` 로 터지지 않음
- **이론적 리스크 제거** — Linux에서의 잠재적 버퍼 타이밍 이슈도 원천 봉쇄

### 관련 리뷰 항목

- 🚨 **D** (PDF splitter NamedTemporaryFile) 해소
- 💡 **S4** (`resolver.py:163-170` `NamedTemporaryFile(delete=False)` → `with` 권장) — 해당 위치는 이미 `tmp.close()` 후 `upload_*` 를 호출하는 패턴이라 D와 같은 버그가 아님. 오히려 `delete=True` + `with` 로 바꾸면 블록 내 fd 가 열린 채 upload 가 호출되어 D 와 동일한 이중 핸들 문제 재현 — **거부**

---

## Fix E. SDK `kiwoom-llm-handler` 의존성 선언 누락 보완

### 배경

`packages/llm-request/sdk/llm_request/__init__.py` 는 `llm_handler` 의 공개 API를 **대량 re-export** 한다 — `LLMResponse`, `StreamChunk`, `Part`, `FileRef`, `call`, `stream`, `upload_file`, `create_cache` 등. `sdk/llm_request/aio.py` 도 `llm_handler.aio` 의 같은 심볼들을 re-export. 따라서 sdk 패키지가 로드되려면 `kiwoom-llm-handler` 가 설치 대상에 **반드시** 포함되어야 한다.

리뷰 원 지적: `packages/llm-request/sdk/pyproject.toml` 의 `[project] dependencies` 에 `kiwoom-llm-handler` 가 **선언되지 않음**. sdk 단독 설치 시 `from llm_request import llm_request_async` 첫 import 지점에서 `ModuleNotFoundError: No module named 'llm_handler'` 로 실패할 수 있는 구조.

### 왜 지금까지 "우연히" 동작했는가 — `uv.sources` vs `dependencies` 구분

두 섹션은 역할이 다르다:

| 섹션 | 위치 | 역할 |
|---|---|---|
| `[tool.uv.sources]` | **워크스페이스 루트** `packages/llm-request/pyproject.toml` | "이 패키지를 설치해야 할 때 **어디서** 가져와라" — 경로 지도 |
| `dependencies = [...]` | **각 멤버** (`sdk/`, `service/`) `pyproject.toml` | "**이게 필요하다**" — 요구 선언 |

현재 루트는 `kiwoom-llm-handler = { path = "../llm-handler/sdk", editable = true }` 로 경로 지도를 이미 갖고 있다. 그리고 `service/pyproject.toml` 에 `kiwoom-llm-handler>=0.1.0` 이 선언돼 있어서 **`uv sync` 를 루트에서 돌리면 service를 통해 llm-handler가 editable로 설치**된다. sdk는 같은 환경 안에서 그 모듈을 import 가능 — 그래서 workspace 전체 설치 시나리오에선 문제가 드러나지 않았다.

문제가 드러나는 시나리오:
- `pip install -e packages/llm-request/sdk` 같은 **sdk 단독 설치**
- sdk만 wheel 빌드해서 배포 → 외부 사용자가 `pip install kiwoom-llm-request` 로 받는 경우

두 경우 모두 sdk의 `dependencies` 만 보므로 llm-handler가 설치 목록에 없다. 첫 import에서 실패 확정.

### 검증 — SDK 코드의 실제 import

sdk 디렉터리에서 `(from|import) llm_handler` 를 grep한 결과 4건 (모두 top-level, 모듈 로드 시 즉시 평가):

```
sdk/llm_request/__init__.py:30 — import llm_handler as llm_handler  # noqa: PLC0414
sdk/llm_request/__init__.py:31 — import llm_handler.aio             # noqa: F401
sdk/llm_request/__init__.py:32 — from llm_handler import (...)      # 대량 re-export
sdk/llm_request/aio.py:14     — from llm_handler.aio import (...)
```

지연 실패 아님. 의존성 누락 시 sdk import 시점에 즉시 깨진다.

### 해결 내용

`sdk/pyproject.toml` 의 dependencies 리스트에서 두 가지 정리를 동시에 수행:

1. **누락된 `kiwoom-llm-handler>=0.1.0` 추가** — 버전 스펙은 service와 동일
2. **dead dep 3개 제거** (`kiwoom-redis-handler`, `kiwoom-file-manager`, `filetype`) — 아래 "dead dep 검증" 참고

```diff
 dependencies = [
     "httpx>=0.27.0",
-    "filetype>=1.2.0",
-    "kiwoom-redis-handler>=1.0.0",
-    "kiwoom-file-manager>=0.1.0",
+    "kiwoom-llm-handler>=0.1.0",
 ]
```

경로(`[tool.uv.sources]`)는 건드리지 않음 — 이미 루트에 `../llm-handler/sdk` 로 매핑돼 있고, 외부 사용자는 배포 index의 `kiwoom-llm-handler` 를 가져오면 됨(배포 방식은 별도 주제).

### dead dep 검증

리뷰어가 지적한 E의 본질은 "필요한 의존성 누락" 이지만, sdk dependencies 를 뜯어보는 과정에서 **sdk 코드가 실제로 import하지 않는** 패키지 3개가 관찰됐다. `(from|import) <pkg>` grep + case-insensitive 전체 문자열 grep 양방향으로 sdk 트리 전체 재검증:

| 패키지 | sdk 코드 import 여부 | 실제 사용 위치 |
|---|---|---|
| `kiwoom-redis-handler` | 0건 | Service 전용 (`service/app/logic/file_processing/{dedup,cache_store}.py`) |
| `kiwoom-file-manager` | 0건 | Service 전용 (`service/app/logic/file_register.py`) |
| `filetype` | 0건 | sdk·service 어느 쪽에서도 실사용 미확인 |

sdk는 순수 HTTP 클라이언트 — Redis / file-manager 접속은 Service 역할이므로 sdk에 이들 SDK를 **직접** 연결할 이유가 없다. 이 3개는 **dead dep**. 같은 파일(`sdk/pyproject.toml`)의 같은 편집 기회에 함께 정리하는 것이 변경 단위 측면에서 자연스럽고, `uv.lock` 갱신 시 관련 transitive 의존성도 같이 빠진다.

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/sdk/pyproject.toml` | `dependencies` 정리 — `kiwoom-llm-handler>=0.1.0` 추가 + dead dep 3개(`kiwoom-redis-handler`, `kiwoom-file-manager`, `filetype`) 제거 |

### 영향 범위

- **Breaking 없음** — 루트 `uv sync` 환경의 설치 결과는 실질적으로 동일 (이전부터 llm-handler가 service를 통해 설치되고 있었음). sdk 단독 설치·빌드·배포 시나리오에서만 설치 목록에 llm-handler가 명시적으로 추가됨
- **런타임 동작 변화 없음** — 코드에서 import하는 대상이 동일
- `uv.lock` 갱신은 `uv sync` 실행 시 자동 반영

### 관련 리뷰 항목

- 🚨 **E** (SDK 의존성 누락) 해소
- ⚠️ **I-A4** (SDK 과도 re-export 축소) — 사용자 결정: SDK 사용자 편의를 위한 의도된 설계. 수정 불필요

---

## Fix G. stream API 를 `tuple` 반환으로 변경 (I-A2)

### 배경

리뷰 원 지적 (I-A2): [`docs/01.DESIGN.md`](01.DESIGN.md) L336 "반환 타입 정리" 표는 스트림 함수도 `tuple[AsyncIterator, dict]` 로 정의했지만 실제 구현은 `AsyncIterator[StreamChunk]` 만 반환 — **스트림 호출자가 `cache_hit` 등 meta 정보에 접근할 방법 없음**.

```python
# 변경 전
async for chunk in llm_request_stream_async(llm_input):
    print(chunk["text"])
# ← 이 코드 어디에서도 cache_hit 을 알 수 없음. non-stream 은 알 수 있는데 stream 만 사각지대
```

### 결정

**tuple 반환**으로 변경하여 non-stream (`llm_request_async` 의 `(response, meta)`) 과 패턴 통일. Meta 는 HTTP 응답 헤더(`X-LLM-Meta`, URL-encoded JSON) 로 전달 — SSE body 는 기존 `chunk` 이벤트만 유지.

```python
# 변경 후
stream, meta = await llm_request_stream_async(llm_input)
print(meta["cache_hit"])          # 스트림 시작 전 즉시 확인 가능
async for chunk in stream:
    print(chunk["text"])
```

### 대안 비교

| 옵션 | 호출 패턴 | Breaking? | 결정 |
|---|---|---|---|
| **(a) tuple 반환** | `stream, meta = await ...` + 순수 `async for` | ✅ | **채택** |
| (b-1) SSE 마지막 이벤트 | `async for event: if event["type"]=="meta"` | ❌ | 기각 — 분기 어수선, cache_hit 을 스트림 다 끝나야 알 수 있음 |
| (b-2) SSE 첫 이벤트 | 동상, 다만 첫 이벤트로 meta | ❌ | 기각 — 여전히 타입 분기 필요, non-stream 과 비대칭 |
| (c) 그대로 | meta 없음 | ❌ | 기각 — 설계 문서와 구현 불일치 유지 |

**Breaking 감수 이유**: Kiwoom-Rag 전체 grep 결과 외부 에이전트에서 `llm_request_stream*` 을 실제 호출하는 코드 **0건** (패키지 내부·테스트·노트북만 존재). non-stream 과의 호출 패턴 일관성이 장기적 이점.

### 해결 내용

#### 1. Service `runner.py` — async generator → async function 변환

기존 `llm_request_stream_async` 는 `yield` 를 포함한 async generator 였음. `_do_llm_stream` 은 async generator 로 유지하되, 최상위 `llm_request_stream_async` 는 async function 으로 바꿔 `(AsyncIterator, meta)` 튜플을 `return`.

4 경로 (캐시 히트 / dc 없음 / leader / follower HIT / timeout) 모두 `(stream, meta)` 튜플 반환하도록 재구성. 캐시 히트 경로엔 **단일 chunk 만 yield 하는 inner async generator** (`_cached_stream`), leader 경로엔 **`release_standby` 를 `finally` 에서 호출하는 inner generator** (`_leader_stream`) 도입.

sync 래퍼 `llm_request_stream` 도 `(Iterator[StreamChunk], dict)` 반환으로 마이그레이션.

#### 2. Service `routers/request.py` — meta 를 `X-LLM-Meta` 헤더로

```python
stream, meta = await llm_request_stream_async(...)

async def _sse():
    async for chunk in stream:
        yield f"data: {json.dumps(event)}\n\n"
    yield "data: [DONE]\n\n"

headers = {"X-LLM-Meta": quote(json.dumps(meta, default=str, ensure_ascii=False))}
return StreamingResponse(_sse(), media_type="text/event-stream", headers=headers)
```

meta 는 URL-encoded JSON 으로 헤더에 담기므로 non-ASCII 값도 안전. SSE body 는 기존 `chunk` 이벤트 + `[DONE]` 마커 그대로.

#### 3. SDK `_client.py` — `httpx.stream()` context 수동 관리

httpx 의 `stream()` 은 기본적으로 context manager 라 `async with` 블록 밖에서 response 를 쥐고 있을 수 없다. tuple 반환을 위해 **`__aenter__` 수동 호출 + body async generator 의 `finally` 에서 `__aexit__`** 패턴 적용.

```python
ctx = self._http.stream("POST", "/v1/request/stream", **stream_kwargs)
resp = await ctx.__aenter__()                # 수동 open

meta_header = resp.headers.get("X-LLM-Meta", "")
meta = json.loads(unquote(meta_header)) if meta_header else {}

async def _iter_events():
    try:
        async for line in resp.aiter_lines():
            ...
            yield json.loads(data)
    finally:
        await ctx.__aexit__(None, None, None)  # 소비 완료 시 close

return _iter_events(), meta
```

에러 경로(4xx 응답)에선 즉시 `ctx.__aexit__` 후 `ServiceError` raise. 연결 실패(`ConnectError` / `TimeoutException`)는 `__aenter__` 시점에 잡아 `ServiceConnectionError`.

#### 4. SDK 공개 함수 (`llm_request_stream_async`, `llm_request_stream`)

SDK `__init__.py` 의 두 공개 함수는 내부 `_client.request_stream` 의 튜플을 그대로 전달. sync 래퍼는 `asyncio.run` 안에서 stream 소비 후 `(iter(events), meta)` 튜플 반환.

#### 5. 테스트 · 노트북 · README 마이그레이션

- `service/tests/logic/test_runner_standby.py` — `async for chunk in runner.llm_request_stream_async(...)` → `stream, _meta = await runner.llm_request_stream_async(...); async for chunk in stream:`
- `quickstarts/verify_all.ipynb` §7, §8 셀 — 동일 패턴. meta 에서 `cache_hit` 출력 추가
- `README.md` — 공개 API 표 반환 타입(`tuple[..., dict]`) + 주의사항 예시 업데이트

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `service/app/logic/runner.py` | `llm_request_stream_async` async function 화 + `llm_request_stream` tuple 반환 |
| 변경 | `service/app/routers/request.py` | `X-LLM-Meta` 응답 헤더 추가 + `urllib.parse.quote` import |
| 변경 | `sdk/llm_request/_client.py` | `request_stream` tuple 반환, httpx stream ctx 수동 관리 + `urllib.parse.unquote` import |
| 변경 | `sdk/llm_request/__init__.py` | `llm_request_stream_async` · `llm_request_stream` tuple 반환 |
| 변경 | `service/tests/logic/test_runner_standby.py` | stream 테스트 tuple unpack 패턴 마이그레이션 |
| 변경 | `quickstarts/verify_all.ipynb` | §7, §8 셀 tuple unpack 패턴 |
| 변경 | `README.md` | 공개 API 표 + 주의사항 예시 |

### 영향 범위

- **SDK 호출 패턴 변경 (Breaking)** — `async for event in llm_request_stream_async(...)` → `stream, meta = await llm_request_stream_async(...); async for event in stream`. Kiwoom-Rag 내 외부 호출자 grep 결과 0건
- **HTTP 프로토콜 확장 (non-breaking)** — 응답에 `X-LLM-Meta` 헤더 추가. SSE body 포맷은 그대로이므로 헤더를 무시하는 구버전 클라이언트도 body 소비는 가능 (meta 확보 불가)
- **테스트 커버리지** — `test_runner_standby.py` 의 stream 경로 테스트가 새 API 로 검증됨

### 관련 리뷰 항목

- ⚠️ **I-A2** (`llm_request_stream_async` 반환 타입 불일치) 해소

---

## Fix H. 서버 종료 시 file-manager HTTP 클라이언트 close (I-A6)

### 배경

리뷰 원 지적 (I-A6): `providers.py:79` file-manager httpx 싱글톤 미설정 — "매 호출마다 `AsyncClient` 생성·폐기, 연결 재사용 없음".

### 검증 — 지적의 전제가 사실과 다름

실제로 [`FileManagerHandler`](../../file-manager/sdk/file_manager/handler.py) (file-manager SDK) 는 이미 싱글톤으로 구현돼 있음 — `__new__` + `_initialized` 패턴 (`packages/file-manager/sdk/file_manager/handler.py:60-74`). `fm = FileManagerHandler()` 를 매 요청마다 호출해도 동일 인스턴스 반환하며 내부 `httpx.AsyncClient` 가 재사용됨. **커넥션 풀은 이미 정상 동작**.

다만 한 가지 누락: 서버 종료 시 `fm.close()` 호출이 없음. FastAPI `lifespan` 의 `yield` 이후(shutdown) 구간이 비어 있어 프로세스 종료 시 내부 `httpx.AsyncClient` 가 명시적으로 `aclose()` 되지 않음. OS 가 소켓을 정리해주긴 하지만 명시적 정리가 정석.

### 결정

리뷰 지적 중 "커넥션 재사용" 주장은 이미 반영된 상태 → 그 부분은 수정 불필요. **close 누락 부분만** lifespan shutdown 구간에 추가.

### 해결 내용

`service/app/main.py`:

```python
from file_manager import FileManagerHandler

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_handlers()
    logger.info("llm-request service 시작")
    yield
    # shutdown — 싱글톤 FileManagerHandler 의 httpx.AsyncClient 정리
    fm = FileManagerHandler()
    await fm.close()
    logger.info("llm-request service 종료")
```

- `FileManagerHandler()` 호출은 싱글톤이라 **이미 만들어진 인스턴스를 반환** — 새로 만들지 않음
- import 는 top-level
- `configure_handlers()` 가 이미 startup 단계에서 handler SDK 들을 초기화하므로 startup 은 별도 변경 없음

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `service/app/main.py` | top-level `from file_manager import FileManagerHandler` + `lifespan` shutdown 에 `await FileManagerHandler().close()` |

### 영향 범위

- **Breaking 없음** — startup 동작 동일. shutdown 시에만 HTTP 클라이언트 명시 close 가 추가됨
- **리소스 정리 개선** — 프로세스 종료 시 소켓 누수 가능성 제거

### 관련 리뷰 항목

- ⚠️ **I-A6** (file-manager httpx 싱글톤 미설정) 해소 — 단, 리뷰어의 "매 호출 생성" 주장은 사실과 다름. 이미 싱글톤이었고, 누락됐던 close 만 추가

---

## Fix I. Redis 동기 호출 → `asyncio.to_thread` 래핑 (I-Q4)

### 배경

`packages/llm-request/service/app/logic/file_processing/dedup.py` 의 `async def` 함수 3곳 — `try_acquire_standby` (L74), `wait_for_standby_release` (L125), `release_standby` (L151) — 이 `redis-handler` 의 raw `client.set/get/delete` 를 **동기로** 호출 중. async 컨텍스트에서 sync I/O 호출은 이벤트 루프를 그 시간 동안 점유 → **같은 워커 위 다른 코루틴들이 await 못 깨어남**.

설계 문서 `docs/08.cache-stampede-dedup.md` 도 이 상태를 "임시 방편" 으로 인지 중. 본 PR에서 정리.

### 영향 분석 — "Redis 자체가 더 빨라지는 게 아님"

핵심: 이건 **Redis 서버를 빠르게 만드는 게 아니라, 호출자(우리)의 이벤트 루프가 안 막히게 하는** 문제. 같은 워커의 다른 코루틴 보호가 목적.

| 시나리오 | 블로킹 영향 |
|---|---|
| 동시 요청 1~10개 | 누적 50ms 이내 — 사용자 체감 0 |
| 동시 요청 100개 | 누적 500ms 직렬화 — 측정 가능 |
| Redis 응답 50ms+ 로 느려질 때 | 비선형 증폭 (5초+ 가능) |
| **LLM 호출 (2~30초) 대비 비율** | **0.02~0.2%** — 평소엔 무시, 단 안전 마진 가치 있음 |

### 해결 옵션 비교

| 옵션 | 변경 | 채택? |
|---|---|---|
| **(1) `asyncio.to_thread` 래핑** | sync 함수를 스레드풀로 빼서 `await` 가능. 변경 작음 | **✅** |
| (2) `redis.asyncio.Redis` 직접 사용 | dedup만 redis-handler 우회. 진짜 async I/O | dedup 만 별도 client 관리, 일관성 깨짐 |
| (3) 유지 + follow-up | 변경 없음 | 본 PR 옵션 D (전부 처리) 방침과 불일치 |

(1) 은 **redis-handler 가 미래에 동기 SETNX 헬퍼를 제공해도 동일 패턴 그대로 활용 가능** — "sync 함수를 비동기 친화적으로 감싼다" 는 개념이라 함수 출처(raw client / 헬퍼) 와 무관.

### 해결 내용

```diff
 # dedup.py:74 (try_acquire_standby)
-result = client.set(standby_key, value, nx=True, ex=ttl_seconds)
+result = await asyncio.to_thread(
+    client.set, standby_key, value, nx=True, ex=ttl_seconds,
+)

 # dedup.py:125 (wait_for_standby_release loop)
-if client.get(standby_key) is None:
+if await asyncio.to_thread(client.get, standby_key) is None:
     return None

 # dedup.py:151 (release_standby)
-client.delete(standby_key)
+await asyncio.to_thread(client.delete, standby_key)
```

`import asyncio` 는 이미 모듈 상단에 존재해 추가 불필요. 함수 시그니처·호출자 변화 0.

### `redis-handler` 측 변경 0

`redis-handler` 는 sync-only 패키지이고 다른 handler 사용처와 인터페이스 협의가 필요해 본 PR 범위 밖. async 버전 추가는 별도 결정 사안 (`packages/redis-handler/` 측 follow-up). 우리는 **호출자 책임 범위에서만 비동기 친화화** 적용.

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/file_processing/dedup.py` | sync redis 호출 3곳을 `asyncio.to_thread` 로 래핑 |

### 영향 범위

- **Breaking 없음** — `dedup.py` 함수 시그니처 동일, 호출자 변화 0
- **이벤트 루프 보호** — sync 호출이 스레드풀로 빠져 같은 워커의 다른 코루틴 await 깨우기 안 막힘
- **스레드 컨텍스트 스위치 비용 ~10μs** 추가 — 무시 가능

### 관련 리뷰 항목

- ⚠️ **I-Q4** (Async 함수에서 sync Redis 사용) 해소

---

## Fix J. `cache_store.py` BaseJsonStore 캡슐화 정리 (I-Q5)

### 배경

`cache_store.py` 의 모듈 함수 6개 (`find_files_api`, `save_files_api`, `delete_files_api`, 그리고 `*_context_cache` 3개) 가 `BaseJsonStore` 의 protected 메서드(`_make_key`, `_get_json`, `_set_json`, `_delete`) 를 **외부 모듈에서 직접** 호출 중:

```python
# Before
_store = FileCacheStore()  # 모듈 싱글톤

def find_files_api(file_checksum):
    key = _store._make_key("files_api", file_checksum)   # ← 외부에서 protected
    return _store._get_json(key)
```

파이썬 관례상 `_` 로 시작하는 이름 = **"외부에서 호출하지 말라"** 는 표시. `BaseJsonStore` 가 내부 구현을 변경하면 cache_store 가 동시에 깨지는 강한 결합.

### 검증 — `BaseJsonStore` 자신이 권장하는 패턴

[`packages/redis-handler/redis_handler/base_store.py`](../../redis-handler/redis_handler/base_store.py) docstring (L13-23) 자체가 정상 사용법을 명시:

```python
사용법:
    class MyStore(BaseJsonStore):
        def save(self, item_id: str, data: dict) -> bool:
            return self._set_json(self._make_key(item_id), data)
```

→ **자식 클래스 안에서 `self._make_key` 호출이 정상 패턴**. README.md 의 chat-handler 예시도 동일. 외부 모듈 함수의 `_store._make_key(...)` 접근은 base_store 가 의도한 캡슐화 방향과 위배.

### 옵션 비교

| 옵션 | 의미 | 평가 |
|---|---|---|
| ~~(a) redis-handler에 public helper 추가~~ | `make_key()`, `get_json()` 같은 public 노출 추가 | base_store 의도와 **역행** — 다른 사용처도 외부 호출을 유도. 캡슐화 더 약화 |
| **(b) 클래스 메서드로 이동** | `cache_store.py` 의 모듈 함수 6개를 `FileCacheStore(BaseJsonStore)` 메서드로 옮기고 `self._make_key/_get_json/...` 호출. 모듈 함수는 위임으로 유지 | base_store 의도와 일치, llm-request 안에서 완결 |

→ **(b)** 채택.

### 해결 내용

```diff
 class FileCacheStore(BaseJsonStore):
     def __init__(self) -> None:
         super().__init__(prefix="file_cache")

+    # 도메인 메서드 6개를 클래스 안에 정의 — self._make_key/_get_json/... 호출
+    def find_files_api(self, file_checksum: str) -> dict | None:
+        return self._get_json(self._make_key("files_api", file_checksum))
+
+    def save_files_api(self, file_checksum, doc, ttl=FILES_API_TTL) -> None:
+        self._set_json(self._make_key("files_api", file_checksum), doc, ttl=ttl)
+
+    def delete_files_api(self, file_checksum: str) -> None:
+        self._delete(self._make_key("files_api", file_checksum))
+
+    # ... find/save/delete_context_cache 도 같은 패턴으로

 _store = FileCacheStore()

-# 모듈 함수가 _store._private 을 직접 호출 (캡슐화 위반)
-def find_files_api(file_checksum):
-    key = _store._make_key("files_api", file_checksum)
-    return _store._get_json(key)
+
+# 모듈 함수는 호출자(resolver.py 등) 호환성 유지를 위한 단순 위임
+def find_files_api(file_checksum):
+    return _store.find_files_api(file_checksum)
```


### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/file_processing/cache_store.py` | 도메인 메서드 6개를 `FileCacheStore` 클래스 안으로 이동, 모듈 함수는 위임으로 유지 |

### 영향 범위

- **Breaking 없음** — 모듈 함수 시그니처/호출 패턴 동일. 호출자(resolver.py, runner.py 등) 변화 0
- **캡슐화 회복** — `BaseJsonStore` 의 protected 메서드를 외부에서 호출하지 않음. base_store 가 내부 구현을 바꿔도 cache_store 영향 격리

### 관련 리뷰 항목

- ⚠️ **I-Q5** (`cache_store.py` 의 `BaseJsonStore` private 직접 호출) 해소

---

## Fix K. context_cache 생성 시 model None 검증 (I-Q7)

### 배경

타입 흐름이 좁아지는 호출 체인 — silent runtime error 위험:

```
runner: prep.llm_input.get("model")          # str | None
  ↓
resolver.resolve_files(model=...)             # str | None
  ↓
resolver._create_and_save_context_cache(model: str | None)
  ↓
uploader.create_context_cache(model: str)    # ← None 받으면 곤란
  ↓
llm_handler.aio.create_cache(model=model)    # Gemini API 호출
```

문제:
- context_cache 생성이 **LLM call 보다 먼저** 일어남 → model None 일 때 Gemini Context Cache API 가 모호한 에러 메시지로 먼저 터짐
- 사용자에게 원인이 잘 안 보임. "model 빠뜨렸다" 가 명확하지 않음

### 결정 — fail-fast (resolver 에서 raise)

| 옵션 | 트레이드오프 |
|---|---|
| **(i) Resolver 에서 명시적 raise** | 명확한 한국어 에러 메시지. Gemini API 호출 전 차단. 일찍 fail-fast |
| (ii) 기본값 채워 전달 | 사용자가 의도한 model 과 다른 cache 생성될 위험 (silent corruption). context cache 는 model 별로 분리되므로 잘못된 모델로 만들면 캐시 무용지물 |

→ **(i)** 채택.

### 해결 내용

(1) `_create_and_save_context_cache` 시그니처를 `model: str | None` → `model: str` 로 좁힘 (uploader 와 일치):

```diff
 async def _create_and_save_context_cache(
     ...,
-    model: str | None,
+    model: str,
     ...
 ) -> str:
```

(2) `_process_files_api` 의 context_cache 분기 진입 직전에 None 체크 + raise:

```diff
 if ctx_cache_active and file_refs:    # S3 도입 조건: context_cache_ttl is not None and > 0 (pr_review_solve.md §S3 참조)
+    if model is None:
+        raise ValueError(
+            "context cache 생성에는 model이 필요합니다 — "
+            "llm_input에 'model' 키를 포함하세요"
+        )
     acquired_cc = await dedup.try_acquire_standby(...)
```

체크는 **context_cache 가 실제로 생성되는 경로에서만** 발동 — `context_cache_ttl` 이 `None` 또는 `0` 인 일반 호출에선 `ctx_cache_active` 가 False 라 model 없어도 통과 (LLM call 단계에서 llm-handler 가 처리).

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/file_processing/resolver.py` | `_create_and_save_context_cache` 시그니처 좁힘 + `_process_files_api` 에 None 체크 |

### 영향 범위

- **Breaking 없음 (정상 경로)** — `llm_input.model` 이 있는 정상 호출은 영향 0
- **실패 경로 개선** — `context_cache_ttl` + `model=None` 조합에서 Gemini API 호출 전에 `ValueError` 로 차단 (한국어 안내 메시지 포함)
- **회귀 방지 테스트**: Fix L 의 `test_resolver.py::TestResolveFiles::test_model_none_with_context_cache_ttl_raises` 에서 검증

### 관련 리뷰 항목

- ⚠️ **I-Q7** (`create_context_cache` model 타입 불일치) 해소

---

## Fix L. 단위 테스트 추가 (I-Q11)

리뷰가 누락 지적한 영역에 신규 테스트 파일 3개 추가. 골든 패스 + 핵심 분기 위주, 회귀 방지 목적. 기존 conftest 의 `mock_collection`, `fake_redis`, `fast_backoff`, `_isolate_redis_for_cache_store` (autouse) fixture 재사용.

### 파일 변경 목록

| 유형 | 경로 | 테스트 수 | 커버 범위 |
|------|------|----------|----------|
| 신규 | `packages/llm-request/service/tests/logic/file_processing/test_validators.py` | **10** | `validate_mime_types` (whitelist 통과 / 거부 / 빈 리스트 / 부분 거부 / 화이트리스트 상수 검증) + `validate_file_sizes` (한도 이하 / 정확히 한도 / 초과 / 0바이트 / 빈 리스트) |
| 신규 | `packages/llm-request/service/tests/logic/file_processing/test_resolver.py` | **13** | `classify_files` (비디오 강제 분리 / 20MB 한도 / 50MB 한도 / 합계 초과 분배 / 빈 입력) + `_estimate_non_file_bytes` (정상 / 직렬화 실패 시 0) + `_process_inline` (Part 변환 / 빈 입력) + `resolve_files` (빈 입력 / **Fix K 회귀 방지** model None+ttl raise / model None+ttl없음 OK / 승급 정책) |
| 신규 | `packages/llm-request/service/tests/logic/test_file_register.py` | **6** | `register_files` (cache hit → upload skip / miss → upload / check_cache 예외 → CacheError wrap / upload 예외 → FileUploadError wrap / 빈 리스트 / 혼합 hit-miss) |

총 **29 tests**.

### 영향 범위

- **Breaking 없음** — 신규 파일만 추가. 기존 코드·테스트 수정 0
- **회귀 방지** — 본 PR 의 Fix C/D/I/J/K 가 깨질 경우 해당 테스트가 실패로 잡음
- **테스트 실행**: 기존 `pytest service/tests/ -v` 명령에 자동으로 포함됨 (별도 마커 없음, `asyncio_mode=auto` 적용)

### 관련 리뷰 항목

- ⚠️ **I-Q11** (테스트 누락 — `test_resolver.py`, validators, classify, `_process_*`) 해소

---

## Fix M. SDK `close()` 공개 API 추가 (I-Q3)

### 배경

`packages/llm-request/sdk/llm_request/_client.py` 의 `AsyncClient` 클래스에 `async def close(self)` 정의는 있지만 — **어디서도 호출되지 않음**. agent 프로세스 종료 시:

- `httpx.AsyncClient` 가 명시적으로 `aclose()` 안 됨 → `ResourceWarning: unclosed <httpx.AsyncClient ...>` 로그
- 커넥션 풀이 보유한 keep-alive 연결의 소켓 핸들이 명시적 정리 없이 OS 회수에 맡겨짐
- 장시간 살아있는 agent 프로세스에서 idle 연결 누수 가능

### 결정 — SDK 공개 API 로 노출 + agent 책임으로

agent 프로세스 종료 전 `close()` 호출이 표준. SDK 가 `close` 를 공개 함수로 제공하고 README 에서 명시적으로 안내한다:

- `from llm_request import close` 로 import 가능
- `await close()` 호출 시 싱글톤 `AsyncClient` 의 `aclose()` 실행 + `_instance` 리셋 → 다음 호출 시 재생성 가능

### 해결 내용

**`sdk/llm_request/__init__.py`**:
- 모듈 레벨 공개 `async def close()` 함수 추가 — 내부에서 싱글톤 `_instance.close()` 호출 후 `_instance = None` 으로 리셋
- `__all__` 에 `"close"` 추가

**`README.md`**:
- 공개 API 표에 `close` 행 추가
- "주의사항" 섹션에 "agent 프로세스 종료 전 `close()` 호출" 안내

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/sdk/llm_request/__init__.py` | 공개 `async def close()` 함수 + `__all__` 등록 |
| 변경 | `packages/llm-request/README.md` | API 표에 `close` 행 + 주의사항 안내 추가 |

### 영향 범위

- **Breaking 없음** — 신규 API 추가. 기존 호출 패턴 영향 0
- **리소스 정리 개선** — agent 가 명시적으로 `close()` 호출하면 소켓·풀 정리됨. `ResourceWarning` 제거
- **재생성 가능** — `_instance = None` 리셋으로 close 후 다시 SDK 호출해도 새 client 가 만들어짐 (테스트 시나리오에 유용)

### 관련 리뷰 항목

- ⚠️ **I-Q3** (`AsyncClient.aclose()` 미호출) 해소

---

## Fix N. `_estimate_non_file_bytes` 예외 로깅 (I-Q6)

### 배경

`packages/llm-request/service/app/logic/file_processing/resolver.py` 의 `_estimate_non_file_bytes(llm_input)` 함수가 직렬화 실패 시 **로그 없이 조용히 0 반환**:

```python
# Before
def _estimate_non_file_bytes(llm_input: dict) -> int:
    try:
        return len(json.dumps(llm_input, default=str).encode("utf-8"))
    except Exception:
        return 0  # ← 로그 없음, 추적 불가
```

이 값은 `classify_files()` 의 inline / Files API 한도 계산 기준 (`total = _estimate_non_file_bytes(llm_input)` 으로 시작). 0 반환 시 **"비파일 부분이 0바이트" 로 오판** → inline pool 에 큰 파일까지 들어갈 가능성 → silent corruption.

### 결정 — silent 해소가 본질, 동작(`return 0`) 은 유지

`logger.exception` 추가만으로 **충분**. fallback 값 변경은 안 함. 이유:

- `json.dumps(llm_input, default=str)` 실패 자체가 극히 드문 edge (예: 재귀 참조). 이 상태면 llm_input 자체가 비정상 → LLM 호출도 어차피 실패할 가능성이 높음
- fallback 값을 0 vs 한도값(예: `return limit`) 으로 바꿔도 결국 그 입력으로 LLM 호출하면 다른 곳에서 깨짐. 실익 미미
- 추적 가능성만 확보하면 운영 시 원인 파악 가능

보수적 fallback 강화 (예: `return limit`) 는 별개 결정 사안 → follow-up 으로 분리.

### 해결 내용

```diff
 def _estimate_non_file_bytes(llm_input: dict) -> int:
     try:
         return len(json.dumps(llm_input, default=str).encode("utf-8"))
     except Exception:
+        logger.exception(
+            "_estimate_non_file_bytes 실패 — llm_input JSON 직렬화 불가. "
+            "0 반환 (inline 한도 계산이 낙관적이 될 수 있음). "
+            "이 상태면 llm_input 자체가 정상이 아닐 가능성이 높음"
+        )
         return 0
```

### 파일 변경 목록

| 유형 | 경로 | 비고 |
|------|------|------|
| 변경 | `packages/llm-request/service/app/logic/file_processing/resolver.py` | `_estimate_non_file_bytes` 의 `except` 블록에 `logger.exception` 추가 |

### 영향 범위

- **Breaking 없음** — 반환 동작·시그니처 동일
- **관측성 향상** — 직렬화 실패 시 stack trace + 영향 설명이 로그에 남아 운영 추적 가능
- **회귀 방지 테스트**: Fix L 의 `test_resolver.py::TestEstimateNonFileBytes::test_unserializable_returns_zero` 가 0 반환 동작을 검증 (logger 호출 자체는 검증 안 함)

### 관련 follow-up

- 직렬화 실패 시 보수적 fallback (`return limit` 같은 큰 값) 으로 바꿔서 inline 한도 계산이 안전한 쪽으로 기울게 할지 — 별도 결정 사안 (실익 미미하다는 분석에 따라 우선순위 낮음)

### 관련 리뷰 항목

- ⚠️ **I-Q6** (`_estimate_non_file_bytes` 의 `except Exception: return 0`) 해소

---

# 📌 참고 사항

- `ec3eb6d Merge remote-tracking branch 'origin' into feat/llm_request` — main origin의 winus-handler 리팩토링이 함께 유입된 부수 머지. llm-request 로직과 무관.
- `156c02f test` — `verify_all.ipynb` 재실행 결과(출력 셀 포함) 커밋. 기능 변경 없음.
- **`Base64Field` 정의 중복** (`sdk/llm_request/models.py` ↔ `service/app/models/requests.py`) — **의도적**. SDK 와 Service 는 독립 파이썬 패키지이고 공유 모듈이 없어 양쪽에 동일 정의를 유지. 직렬화 동작(`b64encode`/`b64decode`)이 SDK↔Service HTTP 계약의 일부라 수정 시 양쪽 동기화 필수. 양 파일 주석에 명시. 리뷰 I-Q8 관련.
- **`fetch_by_cache_key` 의 collection 하드코딩** (`store.py:120-121`) — **유지**. `agent_llm` 컬렉션만 사용할 계획이며 다른 컬렉션 조회 요구사항 없음. 리뷰 I-A5 관련.
- **함수 내부 import 7곳** (`runner.py:499, 634` / `providers.py:23, 37, 60, 70-71`) — **유지**. `packages/llm-request/CLAUDE.md` 및 `Kiwoom-Rag/CLAUDE.md` 전체 검토 결과 "함수 내부 import 금지" 규정 없음. 리뷰어 주장의 근거 확인 실패. 기능적 문제 없음. 리뷰 I-Q1 관련.

---

# ➕ 리뷰 외 추가 수정

## orchestrator 제거

### 배경

`service/app/logic/orchestrator.py`는 llm-request가 **라이브러리 시절** 제공하던 `run_many` / `run_many_stream` 병렬 배치 실행 함수의 구현체였다. v0.2.0 서버화(SDK + Service 2-tier) 전환이 완료되면서 배치 호출은 SDK 사용자 쪽에서 `asyncio.gather` 등으로 조합하는 게 더 자연스러워졌고, SDK·Service 런타임 코드에서 `orchestrator` 참조 0건 확인(grep). 폐기 결정.

### 해결 내용

| 유형 | 경로 | 비고 |
|---|---|---|
| 삭제 | `packages/llm-request/service/app/logic/orchestrator.py` | 본체 (선행 작업) |
| 삭제 | `packages/llm-request/service/tests/logic/test_orchestrator.py` | orchestrator import로 `pytest` collect 단계 ImportError 발생 중이었음 |
| 변경 | `packages/llm-request/service/tests/logic/conftest.py` | 주석의 `test_orchestrator.py` 언급 제거 |

### 영향 범위

- **Breaking 없음** — SDK 공개 API(`llm_request_async`, `llm_request_stream_async` 등) 변경 없음

### 관련 리뷰 항목

- ⚠️ **I-A1** (`run_many` / `run_many_stream` 이 `files` 미전달) 해소 — 함수 자체 제거
- ⚠️ **I-A3** (`run_many_stream` 에 `_type: "cache_hit"` 이벤트 누락) 해소 — 함수 자체 제거
- ⚠️ **I-Q9** (`run_many` 예외 시 dangling task) 해소 — 함수 자체 제거

---

## file-manager `file_path` 를 MDB `file_meta` 에 보존

### 배경

Fix A의 stub→v1 교체에서 `FileManagerHandler.check_cache` / `upload_file` 의 새 반환 시그니처(`{"hit", "file_path"}` / `str file_path`)는 흡수했으나, **반환된 `file_path` 자체는 활용하지 않고 버리고 있었다** (Fix A §"After" 코드 라인의 `# 현재는 반환값 미활용` 참조).

`file_path`는 file-manager가 부여한 영구 식별자(UUID + 확장자)로, 추후 동일 파일을 재조회·재참조할 때의 핵심 키다. 캐시 문서와 함께 보존해두면 후속 흐름(예: 캐시 hit 결과의 원본 파일 추적, file-manager → llm-request 역방향 조회)에서 별도 매핑 테이블 없이 바로 사용할 수 있다.

### 해결 내용

| 유형 | 경로 | 비고 |
|---|---|---|
| 변경 | `packages/llm-request/service/app/models/requests.py` | `FileInput`에 `file_path: str \| None = None` 필드 추가 + docstring 업데이트 |
| 변경 | `packages/llm-request/service/app/logic/file_register.py` | `check_cache` hit 시 `fi.file_path = result["file_path"]`, miss 시 `fi.file_path = await fm.upload_file(...)` 로 반환값 캐치 + 모듈 docstring 업데이트 |
| 변경 | `packages/llm-request/README.md` | §10 문서 스키마 표의 `file_meta` 설명에 `file_path` 포함 명시 |

`runner.py` / `store.py` / `_dump_files_meta` 는 변경 불필요 — `_dump_files_meta` 는 `FileInput.model_dump(exclude={"data"})` 결과를 그대로 사용하므로, 새로 추가된 `file_path` 필드가 자동으로 dump되어 MDB 문서의 `file_meta` list 각 원소에 포함된다.

### 영향 범위

- **Breaking 없음** — `FileInput`의 새 필드는 기본값 `None`. 에이전트가 보내는 요청 바디·SDK 시그니처 모두 동일

---

## file-manager docker env 명칭 통일 (`MDB_*` → `MONGODB_*`)

### 배경

`docker-compose.yml` 에서 MongoDB 관련 env 변수 이름이 서비스마다 섞여 있었다:

- `backend`, `mdb-handler`, `llm-request`, `vectorize`: `MONGODB_DATABASE`
- `file-manager`: `MDB_DATABASE` / `MDB_COLLECTION`

`file-manager` 만 prefix가 달라, 운영자가 `.env` 에서 DB 이름을 override할 때 혼동을 유발하고 Fix A §"교체된 구성 요약" 표의 "메타 DB" 행과도 표기가 어긋났다.

의미상으로도 둘 다 "어떤 MongoDB DB/컬렉션을 쓸까" 라는 동일 개념이므로, 다수파인 `MONGODB_*` 로 통일.

### 해결 내용

| 유형 | 경로 | 비고 |
|---|---|---|
| 변경 | `packages/file-manager/service/app/config.py` | pydantic-settings 필드 `mdb_database` / `mdb_collection` → `mongodb_database` / `mongodb_collection` (필드명이 자동으로 `MONGODB_DATABASE` / `MONGODB_COLLECTION` env를 읽게 됨) |
| 변경 | `packages/file-manager/service/app/routers/files.py` | `_mdb_prefix` 조립 시 `settings.mdb_*` → `settings.mongodb_*` 참조 갱신 |
| 변경 | `docker-compose.yml` | `file-manager` 섹션의 env `MDB_DATABASE` / `MDB_COLLECTION` → `MONGODB_DATABASE` / `MONGODB_COLLECTION` |

`mdb-handler` URL 경로(`/v1/{db}/{collection}`) 자체와 기본값(`kw_ai` / `file_cache`)은 그대로 유지 — 이름표만 바뀌었을 뿐 동작·경로·데이터 위치 모두 동일.

### 영향 범위

- **Breaking (운영자 관점)** — `.env` 에 `MDB_DATABASE=...` / `MDB_COLLECTION=...` 을 override 해서 쓰던 환경이 있다면 `MONGODB_DATABASE` / `MONGODB_COLLECTION` 으로 키 이름을 바꿔줘야 한다. override 없이 기본값으로 쓰던 환경은 무영향.
- **Breaking 없음 (런타임)** — 컨테이너 내부에서 쓰이는 DB/컬렉션 이름·HTTP 요청 경로 모두 동일.
- **MDB 스키마 호환** — `file_meta` list 안의 dict에 키 하나가 늘어날 뿐. 기존 문서 조회·역직렬화에 영향 없음
- **서버화 이전부터 누락된 정보를 메우는 변경** — Fix A에서 교체된 v1 SDK가 이미 제공하던 값을 비로소 활용