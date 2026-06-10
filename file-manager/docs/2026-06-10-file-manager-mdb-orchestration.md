# file-manager mdb 오케스트레이션 이동 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **커밋 정책:** 이 레포의 사용자는 모든 커밋을 직접 한다. 각 Task 끝의 "Commit" 단계는 **실행하지 말고**, 변경을 working tree에 남긴 뒤 사용자에게 커밋 지점을 알린다.

**Goal:** 파일 캐시 오케스트레이션(파일+메타DB 엮기)을 file-handler에서 file-manager로 옮겨 llm-manager/llm-handler 패턴과 구조를 통일한다.

**Architecture:** file-manager(라이브러리)가 오케스트레이션을 보유한다 — 자기 핸들러(file-handler)는 내장 `AsyncClient`로 직접, MongoDB는 `mdb-manager` SDK(→ mdb-handler HTTP)로 접근한다. file-handler는 순수 파일 I/O 서버로 슬림화한다. `FileManagerHandler`의 공개 API 5개는 시그니처·반환 스키마를 그대로 유지한다(DM/STT/llm-manager 보호).

**Tech Stack:** Python 3.10+, httpx(AsyncClient), `kiwoom-mdb-manager`(AsyncMongoDBManager), FastAPI(file-handler), pytest + pytest-asyncio.

**참조 설계:** `docs/superpowers/specs/2026-06-10-file-manager-mdb-orchestration-design.md`

---

## 파일 구조 (변경 맵)

**Kiwoom-Manager / file-manager**
- 수정: `file-manager/pyproject.toml` — mdb-manager 의존성 + pytest-asyncio dev 의존성/설정
- 수정: `file-manager/file_manager/exceptions.py` — `MetadataNotFoundError`, `MDBConnectionError` 추가
- 생성: `file-manager/file_manager/_mdb.py` — mdb 접근 래퍼(caller 주입 + 예외 번역)
- 수정: `file-manager/file_manager/handler.py` — `check_cache`/`upload_file`/`get_file`/`close` 내부 재배선
- 생성: `file-manager/tests/test_mdb.py`, `file-manager/tests/test_handler_orchestration.py`

**Kiwoom-Handler / file-handler**
- 삭제: `file-handler/service/app/core/cache.py`
- 수정: `file-handler/service/app/main.py` — `close_mdb_client` lifespan 제거
- 수정: `file-handler/service/app/config.py` — mdb 설정 제거
- 수정: `file-handler/service/app/routers/files.py` — `/check-cache` 제거, `/upload` 슬림, GET 바이트만
- 수정: `file-handler/service/app/models/requests.py`, `models/responses.py` — 캐시 모델 제거
- 수정: `file-handler/service/pyproject.toml` — mdb-manager 의존성 제거
- 생성: `file-handler/service/tests/test_files.py` — 슬림화된 엔드포인트 테스트

---

## PART A — file-manager (Kiwoom-Manager)

### Task 1: file-manager pyproject에 mdb-manager 의존성 + 테스트 설정 추가

**Files:**
- Modify: `Kiwoom-Manager/file-manager/pyproject.toml`

- [ ] **Step 1: 의존성/소스/테스트 설정 추가**

`[project]`의 `dependencies`를 아래로 교체:

```toml
dependencies = [
    "httpx>=0.27.0",
    "kiwoom-mdb-manager @ git+https://github.com/KiwoomAM/Kiwoom-Manager.git#subdirectory=mdb-manager",
]
```

`[project.optional-dependencies]`에 dev 추가(기존 `scripts`는 유지):

```toml
[project.optional-dependencies]
scripts = ["tqdm>=4.66.0"]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]
```

파일 끝에 mdb-manager editable 소스 + pytest 설정 추가:

```toml
[tool.uv.sources]
kiwoom-mdb-manager = { path = "../mdb-manager", editable = true }

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

- [ ] **Step 2: 설치/해석 확인**

Run: `cd Kiwoom-Manager/file-manager && uv sync --extra dev`
Expected: 성공, `kiwoom-mdb-manager`가 editable로 설치됨.

- [ ] **Step 3: import 확인**

Run: `cd Kiwoom-Manager/file-manager && python -c "from mdb_manager.aio import AsyncMongoDBManager; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit (사용자 커밋 지점)**

```bash
git add file-manager/pyproject.toml
git commit -m "build(file-manager): add mdb-manager dep and pytest-asyncio config"
```

---

### Task 2: file-manager 예외에 MetadataNotFoundError / MDBConnectionError 추가

**Files:**
- Modify: `Kiwoom-Manager/file-manager/file_manager/exceptions.py`

- [ ] **Step 1: 예외 클래스 추가**

`exceptions.py` 끝에 추가:

```python
class MetadataNotFoundError(FMError):
    """파일 서버에는 파일이 있으나 MongoDB에 메타데이터가 없는 데이터 정합성 오류.

    정상 흐름에선 발생하지 않아야 한다(업로드 시 반드시 메타데이터가 기록됨).
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(
            f"메타데이터를 찾을 수 없습니다 (file_path={file_path})",
            status_code=500,
        )


class MDBConnectionError(FMError):
    """MDB Handler(메타데이터 저장소) 연결/응답 오류."""

    def __init__(self, message: str = "MDB Handler 연결 실패") -> None:
        super().__init__(message, status_code=503)
```

- [ ] **Step 2: import 확인**

Run: `cd Kiwoom-Manager/file-manager && python -c "from file_manager.exceptions import MetadataNotFoundError, MDBConnectionError; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/exceptions.py
git commit -m "feat(file-manager): add MetadataNotFoundError and MDBConnectionError"
```

---

### Task 3: file_manager/_mdb.py 생성 (mdb 접근 래퍼)

**Files:**
- Create: `Kiwoom-Manager/file-manager/file_manager/_mdb.py`
- Test: `Kiwoom-Manager/file-manager/tests/test_mdb.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_mdb.py`:

```python
# -*- coding: utf-8 -*-
"""file_manager._mdb 단위 테스트."""

import pytest

import file_manager._mdb as mdb_mod
from file_manager.exceptions import MDBConnectionError


class _FakeBound:
    """with_caller(...)가 돌려주는 bound view 흉내."""

    def __init__(self, recorder):
        self._rec = recorder

    async def find_one(self, filter):
        self._rec["find_filter"] = filter
        return self._rec["find_return"]

    async def insert_one(self, document):
        self._rec["insert_doc"] = document
        return self._rec["insert_return"]


class _FakeMdb:
    def __init__(self, recorder):
        self._rec = recorder

    def with_caller(self, caller):
        self._rec["caller"] = caller
        return _FakeBound(self._rec)


@pytest.fixture
def fake_mdb(monkeypatch):
    rec = {"find_return": None, "insert_return": "id-1"}
    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _FakeMdb(rec))
    return rec


async def test_find_metadata_injects_caller(fake_mdb):
    fake_mdb["find_return"] = {"metadata": {"file_path": "abc.xlsx"}}
    doc = await mdb_mod.find_metadata({"metadata.file_path": "abc.xlsx"})
    assert doc == {"metadata": {"file_path": "abc.xlsx"}}
    assert fake_mdb["caller"] == "file-manager"
    assert fake_mdb["find_filter"] == {"metadata.file_path": "abc.xlsx"}


async def test_insert_metadata_injects_caller(fake_mdb):
    new_id = await mdb_mod.insert_metadata({"metadata": {"file_path": "abc.xlsx"}})
    assert new_id == "id-1"
    assert fake_mdb["caller"] == "file-manager"
    assert fake_mdb["insert_doc"] == {"metadata": {"file_path": "abc.xlsx"}}


async def test_find_metadata_translates_connection_error(monkeypatch):
    from mdb_manager.exceptions import MDBConnectionError as SdkErr

    class _Boom:
        def with_caller(self, caller):
            return self

        async def find_one(self, filter):
            raise SdkErr("down")

    monkeypatch.setattr(mdb_mod, "_get_mdb", lambda: _Boom())
    with pytest.raises(MDBConnectionError):
        await mdb_mod.find_metadata({"x": 1})
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_mdb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'file_manager._mdb'`

- [ ] **Step 3: 최소 구현 작성**

`file_manager/_mdb.py`:

```python
# -*- coding: utf-8 -*-
"""mdb-handler 접근 래퍼 — file-manager의 메타데이터 오케스트레이션 부분.

file-manager가 자기 핸들러(file-handler)는 내장 클라이언트로, 메타데이터(MongoDB)는
mdb-manager SDK(→ mdb-handler HTTP)로 접근한다. caller는 호출 시점에 with_caller로
per-call 주입한다(llm-manager 패턴). mdb-handler의 file_cache ACL(caller="file-manager")과
일치한다.

base_url/database는 import 시점이 아니라 첫 사용 시점에 env에서 읽는다(동결 방지).
"""

from __future__ import annotations

import os

from mdb_manager.aio import AsyncMongoDBManager
from mdb_manager.exceptions import (
    MDBConnectionError as SdkMDBConnectionError,
    MDBHandlerError as SdkMDBHandlerError,
)

from file_manager.exceptions import MDBConnectionError

DEFAULT_MDB_HANDLER_URL = "http://mdb-handler:8300"
DEFAULT_DATABASE = "kw_ai"
# 캐시 컬렉션은 env로 덮어쓰지 못하게 상수 고정(엉뚱한 컬렉션 사고 방지).
CACHE_COLLECTION = "file_cache"
CALLER = "file-manager"

_mdb: AsyncMongoDBManager | None = None


def _resolve_base_url() -> str:
    return os.environ.get("MDB_HANDLER_URL", DEFAULT_MDB_HANDLER_URL)


def _resolve_database() -> str:
    return os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE)


def _get_mdb() -> AsyncMongoDBManager:
    global _mdb
    if _mdb is None:
        _mdb = AsyncMongoDBManager(
            database=_resolve_database(),
            collection=CACHE_COLLECTION,
            base_url=_resolve_base_url(),
        )
    return _mdb


async def close_mdb() -> None:
    global _mdb
    if _mdb is not None:
        await _mdb.aclose()
        _mdb = None


async def find_metadata(filter: dict) -> dict | None:
    """단일 메타데이터 문서 조회. SDK 예외를 MDBConnectionError로 번역."""
    try:
        return await _get_mdb().with_caller(CALLER).find_one(filter)
    except SdkMDBConnectionError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except SdkMDBHandlerError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.status_code}): {e.message}"
        ) from e


async def insert_metadata(document: dict) -> str:
    """단일 메타데이터 문서 삽입. SDK 예외를 MDBConnectionError로 번역."""
    try:
        return await _get_mdb().with_caller(CALLER).insert_one(document)
    except SdkMDBConnectionError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except SdkMDBHandlerError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.status_code}): {e.message}"
        ) from e
```

- [ ] **Step 4: 통과 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_mdb.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/_mdb.py file-manager/tests/test_mdb.py
git commit -m "feat(file-manager): add _mdb wrapper with per-call caller injection"
```

---

### Task 4: handler.check_cache를 mdb 직접 질의로 재배선

**Files:**
- Modify: `Kiwoom-Manager/file-manager/file_manager/handler.py`
- Test: `Kiwoom-Manager/file-manager/tests/test_handler_orchestration.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_handler_orchestration.py`:

```python
# -*- coding: utf-8 -*-
"""FileManagerHandler 오케스트레이션 테스트 (mdb + file-handler 모킹)."""

import pytest

import file_manager.handler as handler_mod
from file_manager.handler import FileManagerHandler


@pytest.fixture(autouse=True)
def reset_singleton():
    FileManagerHandler.reset()
    yield
    FileManagerHandler.reset()


async def test_check_cache_hit(monkeypatch):
    captured = {}

    async def fake_find(filter):
        captured["filter"] = filter
        return {"metadata": {"file_path": "abc.xlsx"}}

    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    fm = FileManagerHandler()
    result = await fm.check_cache(
        {"file_name": "r.xlsx", "file_mtime": 1713000000, "file_size": 100}
    )
    assert result == {"hit": True, "file_path": "abc.xlsx"}
    assert captured["filter"] == {
        "metadata.file_name": "r.xlsx",
        "metadata.file_mtime": 1713000000,
        "metadata.file_size": 100,
    }


async def test_check_cache_miss(monkeypatch):
    async def fake_find(filter):
        return None

    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    fm = FileManagerHandler()
    result = await fm.check_cache(
        {"file_name": "r.xlsx", "file_mtime": 1, "file_size": 2}
    )
    assert result == {"hit": False, "file_path": None}
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py -v`
Expected: FAIL — `AttributeError` (handler_mod에 `find_metadata` 없음) 또는 check_cache가 여전히 HTTP 호출 시도.

- [ ] **Step 3: handler.py 수정 — import + check_cache 교체**

`handler.py` 상단 import 블록에 추가:

```python
from file_manager._mdb import close_mdb, find_metadata, insert_metadata
from file_manager.exceptions import FMServiceError, MetadataNotFoundError
```

(기존 `from file_manager.exceptions import FMServiceError` 줄은 위 줄로 대체)

`check_cache` 메서드 본문을 교체:

```python
    async def check_cache(self, metadata: dict) -> dict:
        """캐시 체크. 캐시 키 3개로 mdb-handler에 직접 질의한다.

        Args:
            metadata: file_name/file_mtime/file_size를 포함하는 dict

        Returns:
            {"hit": True,  "file_path": "<UUID.확장자>"}  (캐시 히트)
            {"hit": False, "file_path": None}              (캐시 미스)
        """
        filter = {
            "metadata.file_name": metadata["file_name"],
            "metadata.file_mtime": metadata["file_mtime"],
            "metadata.file_size": metadata["file_size"],
        }
        doc = await find_metadata(filter)
        if doc is None:
            return {"hit": False, "file_path": None}
        return {"hit": True, "file_path": doc["metadata"]["file_path"]}
```

- [ ] **Step 4: 통과 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/handler.py file-manager/tests/test_handler_orchestration.py
git commit -m "refactor(file-manager): check_cache queries mdb directly"
```

---

### Task 5: handler.upload_file을 file-handler(슬림) + mdb insert로 재배선

**Files:**
- Modify: `Kiwoom-Manager/file-manager/file_manager/handler.py`
- Test: `Kiwoom-Manager/file-manager/tests/test_handler_orchestration.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_handler_orchestration.py` 끝에 추가:

```python
async def test_upload_file_stores_then_records_metadata(monkeypatch):
    posted = {}
    inserted = {}

    async def fake_post(path, files=None, data=None):
        posted["path"] = path
        posted["files"] = files
        posted["data"] = data
        return {"file_path": "uuid123.xlsx"}

    async def fake_insert(document):
        inserted["doc"] = document
        return "id-1"

    fm = FileManagerHandler()
    monkeypatch.setattr(fm._client, "post", fake_post)
    monkeypatch.setattr(handler_mod, "insert_metadata", fake_insert)

    metadata = {
        "file_name": "r.xlsx",
        "file_mtime": 1713000000,
        "file_size": 100,
        "mime_type": "application/vnd.ms-excel",
        "extension": "xlsx",
    }
    file_path = await fm.upload_file(b"BYTES", metadata)

    assert file_path == "uuid123.xlsx"
    assert posted["path"] == "/v1/files/upload"
    assert posted["data"] == {"extension": "xlsx"}
    assert posted["files"]["file"][0] == "r.xlsx"
    assert inserted["doc"] == {
        "metadata": {
            "file_name": "r.xlsx",
            "file_mtime": 1713000000,
            "file_size": 100,
            "file_path": "uuid123.xlsx",
            "mime_type": "application/vnd.ms-excel",
            "extension": "xlsx",
        }
    }
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py::test_upload_file_stores_then_records_metadata -v`
Expected: FAIL — `data`가 `{"extension": "xlsx"}`가 아니거나 insert가 호출되지 않음.

- [ ] **Step 3: upload_file 교체**

`handler.py`의 `upload_file` 메서드 본문 교체:

```python
    async def upload_file(self, file_data: bytes, metadata: dict) -> str:
        """파일 바이트는 file-handler에 저장하고, 메타데이터는 mdb-handler에 기록.

        Args:
            file_data: 파일 바이너리 데이터
            metadata: {file_name, file_mtime, file_size, mime_type, extension}

        Returns:
            file-handler가 생성한 file_path (UUID.확장자) 문자열
        """
        files = {"file": (metadata["file_name"], file_data)}
        data = {"extension": metadata["extension"]}
        resp = await self._client.post(
            f"{_API_PREFIX}/upload",
            files=files,
            data=data,
        )
        file_path = resp["file_path"]

        document = {
            "metadata": {
                "file_name": metadata["file_name"],
                "file_mtime": metadata["file_mtime"],
                "file_size": metadata["file_size"],
                "file_path": file_path,
                "mime_type": metadata["mime_type"],
                "extension": metadata["extension"],
            }
        }
        await insert_metadata(document)
        return file_path
```

- [ ] **Step 4: 통과 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/handler.py file-manager/tests/test_handler_orchestration.py
git commit -m "refactor(file-manager): upload_file stores bytes then records metadata via mdb"
```

---

### Task 6: handler.get_file을 mdb find + file-handler 바이트로 재배선

**Files:**
- Modify: `Kiwoom-Manager/file-manager/file_manager/handler.py`
- Test: `Kiwoom-Manager/file-manager/tests/test_handler_orchestration.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_handler_orchestration.py` 끝에 추가:

```python
async def test_get_file_returns_bytes_and_metadata(monkeypatch):
    async def fake_find(filter):
        assert filter == {"metadata.file_path": "uuid123.xlsx"}
        return {
            "metadata": {
                "file_name": "r.xlsx",
                "file_mtime": 1713000000,
                "file_size": 100,
                "file_path": "uuid123.xlsx",
                "mime_type": "application/vnd.ms-excel",
                "extension": "xlsx",
            }
        }

    async def fake_get_bytes(path):
        assert path == "/v1/files/uuid123.xlsx"
        return b"BYTES", {}

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    monkeypatch.setattr(fm._client, "get_bytes_with_headers", fake_get_bytes)

    data, meta = await fm.get_file("uuid123.xlsx")
    assert data == b"BYTES"
    assert meta["file_path"] == "uuid123.xlsx"
    assert set(meta.keys()) == {
        "file_name", "file_mtime", "file_size", "file_path", "mime_type", "extension"
    }


async def test_get_file_missing_metadata_raises(monkeypatch):
    from file_manager.exceptions import MetadataNotFoundError

    async def fake_find(filter):
        return None

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "find_metadata", fake_find)
    with pytest.raises(MetadataNotFoundError):
        await fm.get_file("missing.xlsx")
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py -k get_file -v`
Expected: FAIL — get_file이 아직 `X-File-Metadata` 헤더를 파싱하므로 메타가 비거나 예외.

- [ ] **Step 3: get_file 교체**

`handler.py`의 `get_file` 메서드 본문 교체:

```python
    async def get_file(self, file_path: str) -> tuple[bytes, dict]:
        """(바이너리, 6필드 메타데이터) 튜플 반환.

        메타데이터는 mdb-handler에서 조회하고, 파일 바이트는 file-handler에서 받는다.

        Args:
            file_path: file-handler가 반환했던 file_path (예: "a1b2c3.xlsx")

        Returns:
            (file_data, metadata)
              metadata: {file_name, file_mtime, file_size, mime_type, extension, file_path}
        """
        doc = await find_metadata({"metadata.file_path": file_path})
        if doc is None:
            raise MetadataNotFoundError(file_path)
        metadata = doc["metadata"]

        file_data, _headers = await self._client.get_bytes_with_headers(
            f"{_API_PREFIX}/{file_path}"
        )
        return file_data, metadata
```

참고: `json`/`unquote` import가 `get_file`에서만 쓰였다면 미사용으로 남는다. `handler.py`
상단에서 `import json` / `from urllib.parse import unquote`가 다른 곳에서 안 쓰이면 제거한다
(`build_metadata`는 `mimetypes`/`PurePosixPath`만 사용 — `json`, `unquote`는 제거 대상).

- [ ] **Step 4: 통과 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/handler.py file-manager/tests/test_handler_orchestration.py
git commit -m "refactor(file-manager): get_file reads metadata from mdb, bytes from file-handler"
```

---

### Task 7: handler.close()가 mdb 클라이언트도 종료

**Files:**
- Modify: `Kiwoom-Manager/file-manager/file_manager/handler.py`
- Test: `Kiwoom-Manager/file-manager/tests/test_handler_orchestration.py`

- [ ] **Step 1: 실패하는 테스트 추가**

끝에 추가:

```python
async def test_close_also_closes_mdb(monkeypatch):
    closed = {"mdb": False, "client": False}

    async def fake_close_mdb():
        closed["mdb"] = True

    async def fake_client_close():
        closed["client"] = True

    fm = FileManagerHandler()
    monkeypatch.setattr(handler_mod, "close_mdb", fake_close_mdb)
    monkeypatch.setattr(fm._client, "close", fake_client_close)

    await fm.close()
    assert closed == {"mdb": True, "client": True}
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/test_handler_orchestration.py::test_close_also_closes_mdb -v`
Expected: FAIL — `close_mdb`가 호출되지 않음.

- [ ] **Step 3: close 교체**

`handler.py`의 `close` 메서드 본문 교체:

```python
    async def close(self) -> None:
        """HTTP 클라이언트 + mdb 클라이언트 연결 종료."""
        if self._client is not None:
            await self._client.close()
        await close_mdb()
        logger.info("FileManagerHandler 연결 종료")
```

- [ ] **Step 4: 전체 통과 확인**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/ -v`
Expected: PASS (test_mdb 3 + test_handler_orchestration 6 + 기존 test_migrate)

- [ ] **Step 5: Commit (사용자 커밋 지점)**

```bash
git add file-manager/file_manager/handler.py file-manager/tests/test_handler_orchestration.py
git commit -m "refactor(file-manager): close() also closes mdb client"
```

---

## PART B — file-handler (Kiwoom-Handler)

> file-handler API는 breaking 변경이지만 유일 소비자가 file-manager다(설계 §3에서 확인). Part A 완료 후 진행한다.

### Task 8: file-handler에서 mdb 의존 제거 (cache.py / main.py / config.py / pyproject)

**Files:**
- Delete: `Kiwoom-Handler/file-handler/service/app/core/cache.py`
- Modify: `Kiwoom-Handler/file-handler/service/app/main.py`
- Modify: `Kiwoom-Handler/file-handler/service/app/config.py`
- Modify: `Kiwoom-Handler/file-handler/service/pyproject.toml`

- [ ] **Step 1: cache.py 삭제**

Run: `git -C Kiwoom-Handler rm file-handler/service/app/core/cache.py`

- [ ] **Step 2: main.py에서 close_mdb_client 제거**

`main.py`에서 `from app.core.cache import close_mdb_client` 줄 삭제. lifespan 함수 안의
`await close_mdb_client()` 줄 삭제. (lifespan에 다른 정리 작업이 없으면 본문을 `yield`만 남긴다.)

수정 후 lifespan 예시:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("File Handler Service 시작")
    yield
    logger.info("File Handler Service 종료")
```

- [ ] **Step 3: config.py에서 mdb 설정 제거**

`Settings`에서 `mdb_handler_url`, `mongodb_database` 필드 삭제. 파일 하단의
`MONGODB_CACHE_COLLECTION = "file_cache"` 상수 삭제. `file_storage_root` 관련 설정은 유지.

수정 후 `Settings`:

```python
class Settings(BaseSettings):
    # File Server — core/storage.py가 os.environ에서 읽는다
    file_storage_root: str = "/data"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

os.environ.setdefault("FILE_STORAGE_ROOT", settings.file_storage_root)
```

- [ ] **Step 4: pyproject에서 mdb-manager 의존성 제거**

`service/pyproject.toml`의 `dependencies`에서 `kiwoom-mdb-manager ...` 줄과,
있다면 `[tool.uv.sources]`의 해당 줄을 삭제. `description`도 "file I/O + mdb 오케스트레이션"
→ "파일 저장/반환 서버 (순수 파일 I/O)"로 수정.

- [ ] **Step 5: import 깨짐 확인**

Run: `cd Kiwoom-Handler/file-handler/service && python -c "import app.config; import app.main"`
Expected: 에러 없음. (있다면 cache 참조가 남은 것 — 추적해 제거)

- [ ] **Step 6: Commit (사용자 커밋 지점)**

```bash
git add file-handler/service/app/main.py file-handler/service/app/config.py file-handler/service/pyproject.toml
git commit -m "refactor(file-handler): drop mdb dependency and cache orchestration module"
```

---

### Task 9: routers/files.py 슬림화 + 모델 정리

**Files:**
- Modify: `Kiwoom-Handler/file-handler/service/app/routers/files.py`
- Modify: `Kiwoom-Handler/file-handler/service/app/models/requests.py`
- Modify: `Kiwoom-Handler/file-handler/service/app/models/responses.py`
- Test: `Kiwoom-Handler/file-handler/service/tests/test_files.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`service/tests/test_files.py` (디렉토리 신규 생성, `tests/__init__.py`도 빈 파일로 생성):

```python
# -*- coding: utf-8 -*-
"""슬림화된 file-handler 엔드포인트 테스트 (mdb 의존 없음)."""

import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_STORAGE_ROOT", str(tmp_path))
    from app.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_upload_then_get_roundtrip(client):
    resp = await client.post(
        "/v1/files/upload",
        files={"file": ("r.xlsx", b"BYTES")},
        data={"extension": "xlsx"},
    )
    assert resp.status_code == 200
    file_path = resp.json()["file_path"]
    assert file_path.endswith(".xlsx")

    got = await client.get(f"/v1/files/{file_path}")
    assert got.status_code == 200
    assert got.content == b"BYTES"
    await client.aclose()


async def test_check_cache_endpoint_removed(client):
    resp = await client.post(
        "/v1/files/check-cache",
        json={"file_name": "r.xlsx", "file_mtime": 1, "file_size": 2},
    )
    assert resp.status_code == 404
    await client.aclose()
```

`service/pyproject.toml`에 dev 의존성/pytest 설정이 없으면 추가:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.24.0"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["."]
```

- [ ] **Step 2: 실패 확인**

Run: `cd Kiwoom-Handler/file-handler/service && uv sync --extra dev && pytest tests/test_files.py -v`
Expected: FAIL — get 응답이 octet-stream 바이트가 아니거나(헤더 의존), check-cache가 아직 404가 아님.

- [ ] **Step 3: routers/files.py 교체**

파일 전체를 아래로 교체:

```python
# -*- coding: utf-8 -*-
"""
File Handler Service 파일 관리 라우터 (순수 파일 I/O)

엔드포인트:
  POST /v1/files/upload        — 파일 저장, file_path 반환
  GET  /v1/files/{file_path}   — 파일 바이트 반환
"""

import logging
import uuid

from fastapi import APIRouter, File, Form, Response, UploadFile

from app.core.storage import read_file, save_file
from app.models.responses import UploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/files", tags=["files"])


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    extension: str = Form(...),
):
    """파일 저장 → file_path 반환. 메타데이터는 file-manager가 mdb에 기록한다."""
    file_path = f"{uuid.uuid4().hex}.{extension}" if extension else uuid.uuid4().hex
    file_data = await file.read()
    await save_file(file_path, file_data)
    logger.info("파일 저장 완료: %s", file_path)
    return UploadResponse(file_path=file_path)


@router.get("/{file_path:path}")
async def get_file(file_path: str):
    """File Server에서 파일 바이트를 꺼내 반환."""
    file_data = await read_file(file_path)
    return Response(content=file_data, media_type="application/octet-stream")
```

- [ ] **Step 4: 모델 정리**

`models/requests.py`: `CheckCacheRequest` 클래스 삭제. 다른 요청 모델이 없으면 파일을 모듈
docstring만 남기거나, import하는 곳이 없으면 빈 모듈로 둔다.

`models/responses.py`: `CacheCheckResponse` 클래스 삭제. `UploadResponse`만 유지:

```python
# -*- coding: utf-8 -*-
"""File Handler Service 응답 모델"""

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """업로드 응답 — 생성된 file_path만 반환"""

    file_path: str
```

- [ ] **Step 5: 통과 확인**

Run: `cd Kiwoom-Handler/file-handler/service && pytest tests/test_files.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit (사용자 커밋 지점)**

```bash
git add file-handler/service/app/routers/files.py file-handler/service/app/models/ file-handler/service/tests/ file-handler/service/pyproject.toml
git commit -m "refactor(file-handler): slim to pure file I/O (remove check-cache, metadata header)"
```

---

## PART C — 마무리 검증

### Task 10: 양 레포 전체 테스트 + 문서/소비자 점검

- [ ] **Step 1: file-manager 전체 테스트**

Run: `cd Kiwoom-Manager/file-manager && pytest tests/ -v`
Expected: 전부 PASS.

- [ ] **Step 2: file-handler 전체 테스트**

Run: `cd Kiwoom-Handler/file-handler/service && pytest tests/ -v`
Expected: 전부 PASS.

- [ ] **Step 3: 공개 API 회귀 점검 (수동)**

확인: `FileManagerHandler`의 `build_metadata`/`check_cache`/`upload_file`/`get_file`/`register_or_get`
시그니처·반환 스키마가 변경 전과 동일한지 diff로 검토. (`register_or_get`은 내부적으로
`check_cache`+`upload_file`만 호출하므로 코드 변경 없음 — 동작만 재확인.)

- [ ] **Step 4: 잔존 참조 점검**

Run: `grep -rn --include='*.py' -E "check-cache|x-file-metadata|cache import|close_mdb_client" Kiwoom-Handler/file-handler`
Expected: 결과 없음(테스트의 `test_check_cache_endpoint_removed` 제외).

- [ ] **Step 5: 문서 갱신 (선택)**

`file-handler/README.md`, `file-handler/CLAUDE.md`, `file-manager/README.md`, `file-manager/CLAUDE.md`,
`file-handler/file-manager-pipeline.md`에서 "file-handler가 mdb를 호출/오케스트레이션" 서술을
"file-manager가 mdb 오케스트레이션 보유"로 갱신.

- [ ] **Step 6: Commit (사용자 커밋 지점)**

```bash
git -C Kiwoom-Manager add -A && git -C Kiwoom-Manager commit -m "docs: file-manager owns cache orchestration"
git -C Kiwoom-Handler add -A && git -C Kiwoom-Handler commit -m "docs: file-handler is pure file I/O"
```

---

## Self-Review 결과

- **Spec 커버리지:** §3 불변식→Task 1·4·5·6·10(공개 API 유지+회귀 점검). §4.1 file-manager→Task 1~7. §4.2 file-handler→Task 8·9. §5 데이터 흐름→Task 4·5·6. §6 에러 처리→Task 2·3·6. §7 테스트→각 Task의 테스트 + Task 9·10. §8 배포→Part B 주석. §9 non-goals(직접 Mongo 연결 안 함)→`_mdb.py`가 mdb-manager 경유. 누락 없음.
- **Placeholder:** 없음(모든 코드 단계에 실제 코드 포함).
- **타입 일관성:** `find_metadata`/`insert_metadata`/`close_mdb`(`_mdb.py`)가 Task 3 정의 그대로 Task 4·5·6·7에서 사용. `metadata` 6필드 키 집합 Task 5(insert)·6(get) 일치. `UploadResponse.file_path` Task 9 일치.
