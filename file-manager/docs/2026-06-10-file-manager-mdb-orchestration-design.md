# 설계: 파일 캐시 오케스트레이션을 file-manager로 이동

- 작성일: 2026-06-10
- 대상 레포: `Kiwoom-Manager` (file-manager), `Kiwoom-Handler` (file-handler)
- 참조 패턴: `llm-manager` / `llm-handler`

## 1. 배경 / 문제

현재 파일 캐시의 **오케스트레이션(파일 + 메타DB를 엮는 판단)이 매니저가 아니라 핸들러
안에** 들어가 있다.

- `file-handler`(서버)가 `app/core/cache.py`에서 `mdb-manager` SDK를 import해 **mdb-handler를
  HTTP로 호출**한다. `check-cache` / `upload` / `get_file` 세 엔드포인트 모두에서 호출이 일어난다.
- `cache.py`의 docstring 스스로 이를 "서버의 매니저 부분"이라 부르며,
  *"핸들러는 핸들러를 호출하지 않는다"* 원칙을 어기고 있음을 인정한다.
- `file-manager`(SDK)는 file-handler로 가는 얇은 HTTP 클라이언트일 뿐, mdb 의존성이 없다.

즉 현재 흐름: `caller → file-manager(얇은 wrapper) → file-handler(파일 I/O + mdb 오케스트레이션)`.

### 따라가려는 패턴 (llm)

- `llm-manager`(라이브러리)는 `mdb-manager`, `redis-manager`, `file-manager`를 editable 의존성으로
  가지며, 핸들러 클라이언트를 `llm_manager._internal.handler_client`로 흡수했다.
- `llm-handler`(서버)는 다른 매니저/핸들러를 전혀 import하지 않는 순수 서버다.
- 즉 오케스트레이션은 **매니저 라이브러리**에 있고, 핸들러는 단일 자원만 다루는 서버다.

## 2. 목표 / 원칙

**핸들러 = 자기 자원 하나만 담당하는 서버. 다른 핸들러를 호출하지 않는다.**
**매니저 = 여러 자원을 엮는 오케스트레이션 라이브러리.** 자기 도메인 핸들러는 내장 클라이언트로
직접 호출하고, 다른 도메인 기능은 그 도메인의 매니저를 통해 접근한다.

이 설계 후 `file-manager`가 정확히 그 모양이 된다:

```
              ┌─ check_cache → mdb-manager.find_one          ─┐
file-manager ─┼─ upload_file → file-handler(저장) + mdb.insert ┤ ← 오케스트레이션
   (library)  └─ get_file    → mdb.find_one + file-handler(읽기)┘
                     │                              │
                     ▼                              ▼
                mdb-handler                   file-handler
                (MongoDB)                     (디스크 I/O만, mdb 의존 0)
```

## 3. 불변식 (가장 중요한 제약)

`FileManagerHandler`의 공개 API 5개는 **시그니처와 반환 스키마를 그대로 유지한다.**

| 메서드 | 시그니처 | 반환 |
|---|---|---|
| `build_metadata` | `(file_name, file_mtime, file_size)` | 5필드 dict |
| `check_cache` | `(metadata: dict)` | `{"hit": bool, "file_path": str|None}` |
| `upload_file` | `(file_data: bytes, metadata: dict)` | `file_path: str` |
| `get_file` | `(file_path: str)` | `(bytes, 6필드 metadata dict)` |
| `register_or_get` | `(bytes_data: bytes, metadata: dict)` | 6필드 dict |

근거: `file-manager`의 공개 API 소비자는 `Kiwoom-DM`, `Kiwoom-STT`, `llm-manager` 셋이며,
모두 위 메서드만 사용한다. 공개 계약을 유지하면 소비자는 영향받지 않는다. 바뀌는 것은
**메서드 내부 구현**과 **file-handler**뿐이다.

추가 확인: `file-handler`의 엔드포인트(`check-cache`, `X-File-Metadata` 헤더 등)를 직접 호출하는
외부 소비자는 없다(`file-manager`가 유일한 소비자). 따라서 file-handler API 변경의 영향 범위는
file-manager로 한정된다.

## 4. 컴포넌트 변경

### 4.1 file-manager (Kiwoom-Manager)

- **`pyproject.toml`**
  - `dependencies`에 `kiwoom-mdb-manager @ git+...#subdirectory=mdb-manager` 추가.
  - `[tool.uv.sources]`에 `kiwoom-mdb-manager = { path = "../mdb-manager", editable = true }` 추가.
  - (llm-manager의 mdb 의존 방식과 동일.)
- **신규 `file_manager/_mdb.py`** — 현 file-handler `cache.py`의 매니저 로직을 흡수.
  - 모듈 레벨 lazy 싱글톤 `AsyncMongoDBManager(database=<MONGODB_DATABASE>, collection="file_cache",
    base_url=<MDB_HANDLER_URL>)`.
  - `find_metadata(filter) -> dict | None`, `insert_metadata(document) -> str` 제공.
  - **caller는 호출 시점에 `.with_caller("file-manager")`로 per-call 주입**(llm-manager 패턴).
    싱글톤/호스트 정체성을 건드리지 않음. mdb-handler의 `file_cache` ACL(`caller="file-manager"`)과
    일치하므로 권한 변화 없음.
  - mdb SDK 예외(`MDBConnectionError`, `MDBHandlerError`)를 file-manager의 `MDBConnectionError`로 번역.
  - `close()` 가능한 정리 함수 제공.
- **`handler.py`** — 공개 시그니처 불변, 내부만 재배선.
  - `check_cache`: file-handler `/check-cache` 호출 대신
    `_mdb.find_metadata({"metadata.file_name":…, "metadata.file_mtime":…, "metadata.file_size":…})`.
    doc 없으면 `{"hit": False}`, 있으면 `{"hit": True, "file_path": doc["metadata"]["file_path"]}`.
  - `upload_file`: ① file-handler `/upload`(파일 바이트 + extension)로 저장하고 `file_path` 수신
    → ② 6필드 metadata 구성 → `_mdb.insert_metadata({"metadata": metadata})` → `file_path` 반환.
  - `get_file`: ① `_mdb.find_metadata({"metadata.file_path": file_path})`로 6필드 메타 조회
    (None이면 `MetadataNotFoundError`) → ② file-handler GET으로 **바이트만** 수신
    → `(bytes, metadata)` 반환.
  - `close()`: 기존 httpx 클라이언트 + 신규 mdb 클라이언트 둘 다 종료.
- **`exceptions.py`**: `MetadataNotFoundError`, `MDBConnectionError`를 file-manager로 이동/추가.
- **설정**: `MDB_HANDLER_URL`, `MONGODB_DATABASE`(기본 `kw_ai`)를 env에서 호출 시점에 읽는다
  (import 시점 동결 방지 — 기존 `_client.py`의 `FILE_HANDLER_URL` 처리 방식과 동일). `file_cache`
  컬렉션명은 상수로 고정(env 덮어쓰기 금지).

### 4.2 file-handler (Kiwoom-Handler)

- **삭제**
  - `app/core/cache.py` 전체.
  - `app/main.py`의 `close_mdb_client` import 및 lifespan 호출.
  - `app/config.py`의 `mdb_handler_url`, `mongodb_database`, `MONGODB_CACHE_COLLECTION`.
  - `service/pyproject.toml`의 `kiwoom-mdb-manager` 의존성, Dockerfile/README의 mdb 관련 설정.
- **엔드포인트 슬림화** (`routers/files.py`)
  - `POST /v1/files/check-cache` → **제거** (file-manager가 mdb에 직접 질의).
  - `POST /v1/files/upload` → form은 `file` + `extension`만 수신. 파일 저장 후 `file_path` 반환.
    메타데이터 구성·`insert_metadata` 제거. (file_path의 UUID 생성은 저장소 레이아웃 책임이므로
    file-handler에 유지.)
  - `GET /v1/files/{file_path}` → **바이트만** 반환. `find_metadata`·`X-File-Metadata`·
    `Content-Disposition` 제거.
  - `CheckCacheRequest` / `CacheCheckResponse` 모델 제거, `UploadResponse`만 유지.
- 결과: file-handler는 `core/storage.py`(순수 파일 I/O)만 남는 순수 서버가 된다.

## 5. 데이터 흐름 (변경 후)

1. **check_cache(metadata)**
   `file-manager → mdb-handler(find_one, caller=file-manager)` → hit/file_path.
2. **upload_file(bytes, metadata)**
   `file-manager → file-handler POST /upload(bytes, extension)` → file_path
   → `file-manager → mdb-handler(insert_one 6필드, caller=file-manager)` → file_path.
3. **get_file(file_path)**
   `file-manager → mdb-handler(find_one by file_path)` → 6필드 metadata (없으면 MetadataNotFoundError)
   → `file-manager → file-handler GET /{file_path}` → bytes → `(bytes, metadata)`.

## 6. 에러 처리

- "파일은 있는데 메타 없음" 정합성 검사 책임이 file-manager로 이동
  (`get_file`에서 mdb 조회 None → `MetadataNotFoundError`).
- mdb SDK 예외 → `MDBConnectionError` 번역은 `_mdb.py`로 이동.
- file-handler는 `FileNotFoundOnServerError` / `FileStorageError`(파일 I/O 계열)만 담당.

## 7. 테스트

- **file-manager**
  - `_mdb.py` 단위 테스트: caller(`file-manager`) per-call 주입 검증, 예외 번역 검증.
  - `check_cache` / `upload_file` / `get_file`: mdb-manager와 file-handler 양쪽을 mock해
    오케스트레이션 순서·인자 검증.
  - **회귀 가드**: 공개 API 반환 스키마 스냅샷(특히 `get_file`의 6필드 dict, `check_cache`의
    `{hit, file_path}`) — DM/STT/llm-manager 보호.
  - 기존 file-handler `service/e2e_test.py`의 캐시/업로드/조회 시나리오를 file-manager 쪽으로 재배치.
- **file-handler**
  - mdb mock 제거. storage 단위 테스트 + 슬림화된 엔드포인트(`upload`, `get`) 테스트만 유지.

## 8. 배포 / 호환성

- file-handler API는 **breaking** 변경이지만 유일 소비자가 file-manager이므로, file-manager와
  **함께 배포**(버전 동시 범프)하면 안전하다.
- file-manager가 mdb-handler에 직접 접근하므로, file-manager를 사용하는 런타임(에이전트
  컨테이너 등)에서 `MDB_HANDLER_URL`이 도달 가능해야 한다. llm-manager가 이미 동일 요구를
  가지므로 인프라상 새로운 제약은 아니다.

## 9. 범위 밖 (Non-goals)

- file-manager가 MongoDB에 직접 연결(motor/pymongo)하는 방식은 채택하지 않는다.
  mdb-handler를 단일 게이트웨이로 유지한다(= mdb-manager SDK 경유).
- `file_cache` 외 다른 컬렉션/캐시 정책 변경은 다루지 않는다.
- 저장소 백엔드(로컬 FS → S3 등) 교체는 다루지 않는다(storage.py 책임 그대로).
