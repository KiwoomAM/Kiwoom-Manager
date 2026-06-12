# rdb-manager HTTP SDK 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **커밋 정책(프로젝트 규칙):** 자동 git commit 금지. 각 Task의 "Commit" 스텝은 **메시지만 텍스트로 제시**하고 실제 실행은 사용자에게 맡긴다. 커밋 메시지는 한 줄(subject)만, body·co-author 금지.

**Goal:** `rdb-manager`를 `rdb_handler`/`psycopg` 직결 라이브러리에서 rdb-handler 서비스(`http://192.7.9.45:8700`)에 HTTP 요청을 보내는 httpx SDK로 재작성한다.

**Architecture:** winus-manager와 1:1 동형 구조. 싱글톤 HTTP 클라이언트(`SyncClient`/`AsyncClient`)를 facade(`__init__.py`/`aio.py`)에서 lazy 생성하고 함수 API가 위임한다. base_url은 `_config.py`에 하드코딩하고 env/`configure(base_url=...)` override를 무시한다.

**Tech Stack:** Python 3.10+, httpx, pydantic v2, pandas, pytest + pytest-asyncio (asyncio_mode=auto).

---

## File Structure

`rdb_manager/` 패키지 최종 구성:

| 파일 | 상태 | 책임 |
|---|---|---|
| `_config.py` | 신규 | `HANDLER_BASE_URL` 단일 출처 |
| `exceptions.py` | 신규 | 호환 예외 + SDK HTTP 예외 |
| `models.py` | 신규 | 서버 응답 pydantic 모델 |
| `_sync_client.py` | 신규 | 동기 httpx 클라이언트 |
| `_client.py` | 신규 | 비동기 httpx 클라이언트 |
| `__init__.py` | 재작성 | 동기 facade |
| `aio.py` | 재작성 | 비동기 facade |
| `query.py` / `async_query.py` / `deps.py` / `async_deps.py` | 삭제 | psycopg 직결 제거 |

`tests/` 최종 구성: `conftest.py`(최소화), `test_config_models.py`, `test_sync_client.py`, `test_async_client.py`, `test_base_url_locked.py`. 기존 `test_aio.py`/`test_async_deps.py`/`test_async_query.py`/`test_integration.py` 삭제.

작업 디렉토리: 모든 경로는 `/home/user/kw/Kiwoom-Manager/rdb-manager/` 기준. 테스트 실행은 해당 디렉토리에서 `python -m pytest`.

---

## Task 1: 테스트 하니스 정리 + leaf 모듈 (config / exceptions / models)

**Files:**
- Delete: `tests/test_aio.py`, `tests/test_async_deps.py`, `tests/test_async_query.py`, `tests/test_integration.py`
- Modify: `tests/conftest.py` (psycopg 모킹 제거 → 최소화)
- Create: `rdb_manager/_config.py`, `rdb_manager/exceptions.py`, `rdb_manager/models.py`
- Test: `tests/test_config_models.py`

- [ ] **Step 1: 기존 psycopg 기반 테스트 삭제 및 conftest 최소화**

```bash
cd /home/user/kw/Kiwoom-Manager/rdb-manager
rm tests/test_aio.py tests/test_async_deps.py tests/test_async_query.py tests/test_integration.py
```

`tests/conftest.py` 전체를 다음으로 교체:

```python
"""rdb-manager 테스트 공용 설정.

HTTP SDK 전환 후에는 psycopg 모킹이 필요 없다. 클라이언트 단위 테스트는
httpx.MockTransport로 핸들러 응답을 모의한다.
"""
```

- [ ] **Step 2: `rdb_manager/_config.py` 생성**

```python
"""rdb_manager 내부 상수 — 핸들러 접속 URL 단일 출처.

운영 정책상 handler 접속 위치는 외부 설정(env / configure(base_url=...))으로
변경할 수 없으며 항상 운영 서버로 고정된다. 동기(__init__)·비동기(aio)
양쪽이 이 값을 공유한다 (엔드포인트 변경 시 이 파일 한 곳만 수정).
"""

HANDLER_BASE_URL: str = "http://192.7.9.45:8700"
```

- [ ] **Step 3: `rdb_manager/exceptions.py` 생성**

```python
"""기존 rdb_manager 호환 예외 인터페이스와 HTTP SDK 전용 예외를 정의한다."""


class DatabaseException(Exception):
    """데이터베이스 관련 기본 예외 (기존 인터페이스 유지)."""

    def __init__(
        self,
        message: str,
        detail: str | None = None,
        sql: str | None = None,
        params: dict | None = None,
    ) -> None:
        self.message = message
        self.detail = detail
        self.sql = sql
        self.params = params
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.detail:
            parts.append(f"상세: {self.detail}")
        return " | ".join(parts)


class ConnectionPoolException(DatabaseException):
    """Connection Pool 관련 예외."""
    pass


class QueryExecutionException(DatabaseException):
    """쿼리 실행 관련 예외."""
    pass


# ── SDK HTTP 전용 예외 ────────────────────────────────────


class SDKError(Exception):
    """SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class HandlerConnectionError(SDKError):
    """Handler 서비스 연결 실패."""

    def __init__(self, message: str = "RDB Handler 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class HandlerError(SDKError):
    """Handler가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(message, status_code)
```

- [ ] **Step 4: `rdb_manager/models.py` 생성**

```python
"""SDK 응답 모델."""

from typing import Any

from pydantic import BaseModel


class DataFrameData(BaseModel):
    """query_to_dataframe 서버 응답."""

    columns: list[str]
    rows: list[list[Any]]
    dtypes: dict[str, str]
    request_id: str


class RowsData(BaseModel):
    """fetch_all 서버 응답."""

    rows: list[list[Any]]
    request_id: str


class DictRowsData(BaseModel):
    """fetch_all_dict 서버 응답."""

    rows: list[dict[str, Any]]
    request_id: str


class OneRowData(BaseModel):
    """fetch_one 서버 응답."""

    row: list[Any] | None
    request_id: str


class DmlResult(BaseModel):
    """DML 실행 결과."""

    rowcount: int
    request_id: str
```

- [ ] **Step 5: 실패 테스트 작성 — `tests/test_config_models.py`**

```python
"""config 상수 잠금 값과 응답 모델 파싱 검증."""

from rdb_manager._config import HANDLER_BASE_URL
from rdb_manager.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)


def test_handler_base_url_is_8700():
    assert HANDLER_BASE_URL == "http://192.7.9.45:8700"


def test_dataframe_data_parses():
    data = DataFrameData.model_validate(
        {"columns": ["a"], "rows": [[1]], "dtypes": {"a": "int64"}, "request_id": "r"}
    )
    assert data.columns == ["a"]
    assert data.rows == [[1]]
    assert data.dtypes == {"a": "int64"}


def test_one_row_data_allows_null():
    data = OneRowData.model_validate({"row": None, "request_id": "r"})
    assert data.row is None


def test_dml_result_parses():
    assert DmlResult.model_validate({"rowcount": 3, "request_id": "r"}).rowcount == 3


def test_rows_and_dict_rows_parse():
    assert RowsData.model_validate({"rows": [[1]], "request_id": "r"}).rows == [[1]]
    assert DictRowsData.model_validate({"rows": [{"a": 1}], "request_id": "r"}).rows == [{"a": 1}]
```

- [ ] **Step 6: 테스트 실행 (config/models 통과 확인)**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_config_models.py -v`
Expected: 5개 PASS. (이 시점에 `import rdb_manager` 자체는 아직 구 `__init__.py`가 `.query`/`rdb_handler`를 import하므로 건드리지 않는다 — 위 테스트는 서브모듈만 직접 import.)

- [ ] **Step 7: Commit (메시지만 제시, 실행은 사용자)**

```
refactor(rdb-manager): config/exceptions/models 추가 + 구 psycopg 테스트 제거
```

---

## Task 2: 동기 HTTP 클라이언트 `_sync_client.py`

**Files:**
- Create: `rdb_manager/_sync_client.py`
- Test: `tests/test_sync_client.py`

- [ ] **Step 1: 실패 테스트 작성 — `tests/test_sync_client.py`**

```python
"""SyncClient 단위 테스트 — httpx.MockTransport로 핸들러 응답 모의."""

import json

import httpx
import pytest

from rdb_manager._sync_client import SyncClient
from rdb_manager.exceptions import HandlerConnectionError, HandlerError


def make_client(handler):
    """MockTransport를 주입한 SyncClient 생성."""
    client = SyncClient(base_url="http://test")
    client._http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


def test_query_to_dataframe_request_and_dtype_restore():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "columns": ["id", "ts"],
            "rows": [[1, "2026-01-01T00:00:00"]],
            "dtypes": {"id": "int64", "ts": "datetime64[ns]"},
            "request_id": "r1",
        })

    df = make_client(handler).query_to_dataframe(
        "SELECT * FROM t WHERE id = %(id)s", {"id": 1}
    )
    assert captured["path"] == "/v1/query/dataframe"
    assert captured["body"] == {
        "query": "SELECT * FROM t WHERE id = %(id)s",
        "params": {"id": 1},
    }
    assert list(df.columns) == ["id", "ts"]
    assert str(df["id"].dtype) == "int64"
    assert str(df["ts"].dtype) == "datetime64[ns]"


def test_fetch_all_returns_tuples():
    def handler(request):
        assert request.url.path == "/v1/query/all"
        return httpx.Response(200, json={"rows": [[1, "a"], [2, "b"]], "request_id": "r"})

    assert make_client(handler).fetch_all("SELECT 1") == [(1, "a"), (2, "b")]


def test_fetch_all_dict_passthrough():
    def handler(request):
        assert request.url.path == "/v1/query/all-dict"
        return httpx.Response(200, json={"rows": [{"a": 1}], "request_id": "r"})

    assert make_client(handler).fetch_all_dict("SELECT 1") == [{"a": 1}]


def test_fetch_one_row_and_none():
    def handler_row(request):
        return httpx.Response(200, json={"row": [1, "a"], "request_id": "r"})

    def handler_none(request):
        return httpx.Response(200, json={"row": None, "request_id": "r"})

    assert make_client(handler_row).fetch_one("SELECT 1") == (1, "a")
    assert make_client(handler_none).fetch_one("SELECT 1") is None


def test_query_body_omits_params_when_none():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rows": [], "request_id": "r"})

    make_client(handler).fetch_all("SELECT 1")
    assert captured["body"] == {"query": "SELECT 1"}


def test_execute_dml_returns_rowcount():
    def handler(request):
        assert request.url.path == "/v1/dml/execute"
        assert json.loads(request.content) == {"sql": "UPDATE t SET x=1", "params": {}}
        return httpx.Response(200, json={"rowcount": 5, "request_id": "r"})

    assert make_client(handler).execute_dml("UPDATE t SET x=1", {}) == 5


def test_execute_many_empty_short_circuits_without_request():
    def handler(request):
        raise AssertionError("빈 params_list는 HTTP 요청을 보내면 안 됨")

    assert make_client(handler).execute_many_dml("INSERT ...", []) == 0


def test_execute_many_sends_params_list():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rowcount": 2, "request_id": "r"})

    rc = make_client(handler).execute_many_dml("INSERT t VALUES (%(v)s)", [{"v": 1}, {"v": 2}])
    assert rc == 2
    assert captured["path"] == "/v1/dml/execute-many"
    assert captured["body"] == {"sql": "INSERT t VALUES (%(v)s)", "params_list": [{"v": 1}, {"v": 2}]}


def test_insert_and_update_endpoints():
    def handler(request):
        assert request.url.path in ("/v1/dml/insert", "/v1/dml/update")
        return httpx.Response(200, json={"rowcount": 1, "request_id": "r"})

    client = make_client(handler)
    assert client.execute_insert("INSERT ...") == 1
    assert client.execute_update("UPDATE ...") == 1


def test_connect_error_maps_to_handler_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(HandlerConnectionError):
        make_client(handler).fetch_all("SELECT 1")


def test_http_400_maps_to_handler_error_with_detail():
    def handler(request):
        return httpx.Response(400, json={"error": "bad sql", "detail": "syntax"})

    with pytest.raises(HandlerError) as exc:
        make_client(handler).execute_dml("BAD")
    assert exc.value.status_code == 400
    assert exc.value.detail == "syntax"


def test_check_database_connection_true_and_false():
    def handler_ok(request):
        assert request.url.path == "/v1/check"
        return httpx.Response(200, json={"connected": True})

    def handler_fail(request):
        raise httpx.ConnectError("down")

    assert make_client(handler_ok).check_database_connection() is True
    assert make_client(handler_fail).check_database_connection() is False
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_sync_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rdb_manager._sync_client'`

- [ ] **Step 3: `rdb_manager/_sync_client.py` 구현**

```python
"""SyncClient — 동기 HTTP 클라이언트."""

import logging
from typing import Any

import httpx
import pandas as pd

from rdb_manager.exceptions import HandlerConnectionError, HandlerError
from rdb_manager.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)

logger = logging.getLogger(__name__)


class SyncClient:
    """RDB Handler 서비스와 통신하는 동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as e:
            raise HandlerConnectionError(f"[RDB 핸들러] 연결 실패: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                detail = body.get("detail")
            except Exception:
                error_msg = response.text
                detail = None
            raise HandlerError(f"[RDB 핸들러] {error_msg}", status_code=response.status_code, detail=detail)

        return response

    def _query_body(self, query: str, params: dict[str, Any] | None) -> dict:
        """SELECT 요청 바디."""
        body: dict[str, Any] = {"query": query}
        if params is not None:
            body["params"] = params
        return body

    def _dml_body(self, sql: str, params: dict[str, Any] | None) -> dict:
        """DML 요청 바디."""
        body: dict[str, Any] = {"sql": sql}
        if params is not None:
            body["params"] = params
        return body

    # ── SELECT ──────────────────────────────────────────

    def query_to_dataframe(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """SELECT → pandas DataFrame."""
        resp = self._request("POST", "/v1/query/dataframe", json=self._query_body(query, params))
        data = DataFrameData.model_validate(resp.json())
        df = pd.DataFrame(data.rows, columns=data.columns)
        for col, dtype in data.dtypes.items():
            if col not in df.columns:
                continue
            try:
                if dtype == "datetime64[ns]":
                    df[col] = pd.to_datetime(df[col])
                elif dtype in ("int64", "float64"):
                    df[col] = pd.to_numeric(df[col])
            except Exception as e:
                logger.warning("컬럼 '%s' dtype 복원 실패 (%s): %s", col, dtype, e)
        return df

    def fetch_all(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[tuple]:
        """SELECT → list of tuples."""
        resp = self._request("POST", "/v1/query/all", json=self._query_body(query, params))
        data = RowsData.model_validate(resp.json())
        return [tuple(row) for row in data.rows]

    def fetch_all_dict(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict]:
        """SELECT → list of dicts."""
        resp = self._request("POST", "/v1/query/all-dict", json=self._query_body(query, params))
        data = DictRowsData.model_validate(resp.json())
        return data.rows

    def fetch_one(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> tuple | None:
        """SELECT → 첫 번째 row 또는 None."""
        resp = self._request("POST", "/v1/query/one", json=self._query_body(query, params))
        data = OneRowData.model_validate(resp.json())
        return tuple(data.row) if data.row is not None else None

    # ── DML ────────────────────────────────────────────

    def execute_dml(
        self, sql: str, params: dict[str, Any] | None = None,
    ) -> int:
        """일반 DML 실행."""
        resp = self._request("POST", "/v1/dml/execute", json=self._dml_body(sql, params))
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_many_dml(
        self, sql: str, params_list: list[dict[str, Any]],
    ) -> int:
        """배치 DML 실행."""
        if not params_list:
            return 0
        resp = self._request(
            "POST", "/v1/dml/execute-many",
            json={"sql": sql, "params_list": params_list},
        )
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_insert(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> int:
        """INSERT 전용."""
        resp = self._request("POST", "/v1/dml/insert", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_update(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> int:
        """UPDATE 전용."""
        resp = self._request("POST", "/v1/dml/update", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    # ── Health ─────────────────────────────────────────

    def check_database_connection(self) -> bool:
        """DB 연결 상태 확인."""
        try:
            resp = self._request("GET", "/v1/check")
            return resp.json().get("connected", False)
        except Exception:
            return False
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_sync_client.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit (메시지만 제시)**

```
refactor(rdb-manager): 동기 HTTP 클라이언트 SyncClient 추가
```

---

## Task 3: 비동기 HTTP 클라이언트 `_client.py`

**Files:**
- Create: `rdb_manager/_client.py`
- Test: `tests/test_async_client.py`

- [ ] **Step 1: 실패 테스트 작성 — `tests/test_async_client.py`**

```python
"""AsyncClient 단위 테스트 — httpx.MockTransport로 핸들러 응답 모의."""

import json

import httpx
import pytest

from rdb_manager._client import AsyncClient
from rdb_manager.exceptions import HandlerConnectionError, HandlerError


def make_client(handler):
    """MockTransport를 주입한 AsyncClient 생성."""
    client = AsyncClient(base_url="http://test")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


async def test_query_to_dataframe_request_and_dtype_restore():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "columns": ["id", "ts"],
            "rows": [[1, "2026-01-01T00:00:00"]],
            "dtypes": {"id": "int64", "ts": "datetime64[ns]"},
            "request_id": "r1",
        })

    df = await make_client(handler).query_to_dataframe(
        "SELECT * FROM t WHERE id = %(id)s", {"id": 1}
    )
    assert captured["path"] == "/v1/query/dataframe"
    assert captured["body"] == {
        "query": "SELECT * FROM t WHERE id = %(id)s",
        "params": {"id": 1},
    }
    assert str(df["id"].dtype) == "int64"
    assert str(df["ts"].dtype) == "datetime64[ns]"


async def test_fetch_all_and_dict_and_one():
    def handler(request):
        path = request.url.path
        if path == "/v1/query/all":
            return httpx.Response(200, json={"rows": [[1, "a"]], "request_id": "r"})
        if path == "/v1/query/all-dict":
            return httpx.Response(200, json={"rows": [{"a": 1}], "request_id": "r"})
        if path == "/v1/query/one":
            return httpx.Response(200, json={"row": [9], "request_id": "r"})
        raise AssertionError(path)

    client = make_client(handler)
    assert await client.fetch_all("SELECT 1") == [(1, "a")]
    assert await client.fetch_all_dict("SELECT 1") == [{"a": 1}]
    assert await client.fetch_one("SELECT 1") == (9,)


async def test_fetch_one_none():
    def handler(request):
        return httpx.Response(200, json={"row": None, "request_id": "r"})

    assert await make_client(handler).fetch_one("SELECT 1") is None


async def test_dml_endpoints_and_rowcount():
    def handler(request):
        assert request.url.path in (
            "/v1/dml/execute", "/v1/dml/insert", "/v1/dml/update",
        )
        return httpx.Response(200, json={"rowcount": 4, "request_id": "r"})

    client = make_client(handler)
    assert await client.execute_dml("UPDATE ...") == 4
    assert await client.execute_insert("INSERT ...") == 4
    assert await client.execute_update("UPDATE ...") == 4


async def test_execute_many_empty_short_circuits():
    def handler(request):
        raise AssertionError("빈 params_list는 요청 금지")

    assert await make_client(handler).execute_many_dml("INSERT ...", []) == 0


async def test_execute_many_sends_list():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rowcount": 2, "request_id": "r"})

    assert await make_client(handler).execute_many_dml("INSERT", [{"v": 1}, {"v": 2}]) == 2
    assert captured["path"] == "/v1/dml/execute-many"
    assert captured["body"] == {"sql": "INSERT", "params_list": [{"v": 1}, {"v": 2}]}


async def test_connect_error_maps():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(HandlerConnectionError):
        await make_client(handler).fetch_all("SELECT 1")


async def test_http_error_maps():
    def handler(request):
        return httpx.Response(500, json={"error": "boom", "detail": "trace"})

    with pytest.raises(HandlerError) as exc:
        await make_client(handler).execute_dml("X")
    assert exc.value.status_code == 500
    assert exc.value.detail == "trace"


async def test_check_connection_true_false():
    def handler_ok(request):
        return httpx.Response(200, json={"connected": True})

    def handler_fail(request):
        raise httpx.ConnectError("down")

    assert await make_client(handler_ok).check_database_connection() is True
    assert await make_client(handler_fail).check_database_connection() is False
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_async_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rdb_manager._client'`

- [ ] **Step 3: `rdb_manager/_client.py` 구현**

```python
"""AsyncClient — 비동기 HTTP 클라이언트."""

import logging
from typing import Any

import httpx
import pandas as pd

from rdb_manager.exceptions import HandlerConnectionError, HandlerError
from rdb_manager.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)

logger = logging.getLogger(__name__)


class AsyncClient:
    """RDB Handler 서비스와 통신하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as e:
            raise HandlerConnectionError(f"[RDB 핸들러] 연결 실패: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                detail = body.get("detail")
            except Exception:
                error_msg = response.text
                detail = None
            raise HandlerError(f"[RDB 핸들러] {error_msg}", status_code=response.status_code, detail=detail)

        return response

    def _query_body(self, query: str, params: dict[str, Any] | None) -> dict:
        body: dict[str, Any] = {"query": query}
        if params is not None:
            body["params"] = params
        return body

    def _dml_body(self, sql: str, params: dict[str, Any] | None) -> dict:
        body: dict[str, Any] = {"sql": sql}
        if params is not None:
            body["params"] = params
        return body

    # ── SELECT ──────────────────────────────────────────

    async def query_to_dataframe(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        resp = await self._request("POST", "/v1/query/dataframe", json=self._query_body(query, params))
        data = DataFrameData.model_validate(resp.json())
        df = pd.DataFrame(data.rows, columns=data.columns)
        for col, dtype in data.dtypes.items():
            if col not in df.columns:
                continue
            try:
                if dtype == "datetime64[ns]":
                    df[col] = pd.to_datetime(df[col])
                elif dtype in ("int64", "float64"):
                    df[col] = pd.to_numeric(df[col])
            except Exception as e:
                logger.warning("컬럼 '%s' dtype 복원 실패 (%s): %s", col, dtype, e)
        return df

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[tuple]:
        resp = await self._request("POST", "/v1/query/all", json=self._query_body(query, params))
        data = RowsData.model_validate(resp.json())
        return [tuple(row) for row in data.rows]

    async def fetch_all_dict(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict]:
        resp = await self._request("POST", "/v1/query/all-dict", json=self._query_body(query, params))
        data = DictRowsData.model_validate(resp.json())
        return data.rows

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> tuple | None:
        resp = await self._request("POST", "/v1/query/one", json=self._query_body(query, params))
        data = OneRowData.model_validate(resp.json())
        return tuple(data.row) if data.row is not None else None

    # ── DML ────────────────────────────────────────────

    async def execute_dml(
        self, sql: str, params: dict[str, Any] | None = None,
    ) -> int:
        resp = await self._request("POST", "/v1/dml/execute", json=self._dml_body(sql, params))
        return DmlResult.model_validate(resp.json()).rowcount

    async def execute_many_dml(
        self, sql: str, params_list: list[dict[str, Any]],
    ) -> int:
        if not params_list:
            return 0
        resp = await self._request(
            "POST", "/v1/dml/execute-many",
            json={"sql": sql, "params_list": params_list},
        )
        return DmlResult.model_validate(resp.json()).rowcount

    async def execute_insert(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> int:
        resp = await self._request("POST", "/v1/dml/insert", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    async def execute_update(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> int:
        resp = await self._request("POST", "/v1/dml/update", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    # ── Health ─────────────────────────────────────────

    async def check_database_connection(self) -> bool:
        try:
            resp = await self._request("GET", "/v1/check")
            return resp.json().get("connected", False)
        except Exception:
            return False
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_async_client.py -v`
Expected: 전부 PASS

- [ ] **Step 5: Commit (메시지만 제시)**

```
refactor(rdb-manager): 비동기 HTTP 클라이언트 AsyncClient 추가
```

---

## Task 4: 동기 facade `__init__.py` 재작성 + base_url 잠금 (동기)

**Files:**
- Modify: `rdb_manager/__init__.py` (전체 재작성)
- Test: `tests/test_base_url_locked.py` (동기 부분)

- [ ] **Step 1: `rdb_manager/__init__.py` 전체 재작성**

```python
# -*- coding: utf-8 -*-
"""rdb_manager — 동기 인터페이스.

PostgreSQL 공개 클라이언트. rdb-handler 서비스(HTTP)에 요청을 보낸다.

사용법:
    import rdb_manager

    df = rdb_manager.query_to_dataframe(
        "SELECT * FROM t WHERE id = %(id)s", {"id": 1}
    )
    rows = rdb_manager.fetch_all_dict("SELECT * FROM t")
    rdb_manager.execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

    import rdb_manager.aio as aio   # async API

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.

Handler 접속 URL은 운영 서버(http://192.7.9.45:8700)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다. timeout 등 기타
설정은 configure()로 조정 가능합니다.
"""

from typing import Any

import pandas as pd

from rdb_manager._config import HANDLER_BASE_URL
from rdb_manager._sync_client import SyncClient
from rdb_manager.exceptions import (
    ConnectionPoolException,
    DatabaseException,
    HandlerConnectionError,
    HandlerError,
    QueryExecutionException,
    SDKError,
)

__all__ = [
    # API — SELECT
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    # API — DML
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    # API — Health
    "check_database_connection",
    # Config / Lifecycle
    "configure",
    "close",
    # Exceptions (기존 호환)
    "DatabaseException",
    "ConnectionPoolException",
    "QueryExecutionException",
    # Exceptions (SDK)
    "SDKError",
    "HandlerConnectionError",
    "HandlerError",
]

_instance: SyncClient | None = None


def _resolve_base_url() -> str:
    return HANDLER_BASE_URL


def _get_client() -> SyncClient:
    global _instance
    if _instance is None:
        _instance = SyncClient(base_url=_resolve_base_url())
    return _instance


def close() -> None:
    """SDK 클라이언트 정리. 프로세스 종료 전에 호출하면 리소스를 깔끔하게 해제한다."""
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8700으로 고정됩니다.
    timeout만 적용됩니다.
    """
    global _instance
    _instance = SyncClient(base_url=_resolve_base_url(), timeout=timeout)


# ── SELECT ──────────────────────────────────────────


def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """SELECT → pandas DataFrame."""
    return _get_client().query_to_dataframe(query, params)


def fetch_all(
    query: str, params: dict[str, Any] | None = None,
) -> list[tuple]:
    """SELECT → list of tuples."""
    return _get_client().fetch_all(query, params)


def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None,
) -> list[dict]:
    """SELECT → list of dicts."""
    return _get_client().fetch_all_dict(query, params)


def fetch_one(
    query: str, params: dict[str, Any] | None = None,
) -> tuple | None:
    """SELECT → 첫 번째 row 또는 None."""
    return _get_client().fetch_one(query, params)


# ── DML ────────────────────────────────────────────


def execute_dml(
    sql: str, params: dict[str, Any] | None = None,
) -> int:
    """일반 DML 실행."""
    return _get_client().execute_dml(sql, params)


def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]],
) -> int:
    """배치 DML 실행."""
    return _get_client().execute_many_dml(sql, params_list)


def execute_insert(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    """INSERT 전용."""
    return _get_client().execute_insert(query, params)


def execute_update(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    """UPDATE 전용."""
    return _get_client().execute_update(query, params)


# ── Health ─────────────────────────────────────────


def check_database_connection() -> bool:
    """DB 연결 상태 확인."""
    return _get_client().check_database_connection()
```

- [ ] **Step 2: 실패 테스트 작성 — `tests/test_base_url_locked.py` (동기 부분)**

`tests/test_base_url_locked.py`를 생성하고 다음 내용을 작성한다 (비동기 테스트는 Task 5에서 추가):

```python
"""Handler URL이 운영 정책상 192.7.9.45:8700으로 고정되어 있고
env / configure(base_url=...) 어느 쪽으로도 우회할 수 없음을 검증한다."""

import importlib

import pytest

LOCKED_URL = "http://192.7.9.45:8700"


@pytest.fixture(autouse=True)
def _reset_modules():
    """매 테스트마다 rdb_manager / rdb_manager.aio 의 _instance를 초기화."""
    import rdb_manager
    import rdb_manager.aio as aio

    rdb_manager.close()
    yield
    rdb_manager.close()
    aio._instance = None


def _get_sync_client(env_value, monkeypatch):
    # RDB_HANDLER_URL: 잠금이 env를 무시함을 검증하기 위한 이름.
    if env_value is None:
        monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    else:
        monkeypatch.setenv("RDB_HANDLER_URL", env_value)

    import rdb_manager
    importlib.reload(rdb_manager)
    return rdb_manager._get_client()


def test_sync_no_env_uses_locked_url(monkeypatch):
    client = _get_sync_client(None, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


@pytest.mark.parametrize(
    "wrong_url",
    ["http://wrong-host:1234", "http://localhost:8700", "http://rdb-handler:8700", ""],
)
def test_sync_env_is_ignored(monkeypatch, wrong_url):
    client = _get_sync_client(wrong_url, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


def test_sync_configure_base_url_is_ignored(monkeypatch):
    monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    import rdb_manager
    importlib.reload(rdb_manager)

    rdb_manager.configure(base_url="http://malicious-host:9999", timeout=10.0)
    client = rdb_manager._get_client()
    assert str(client._http.base_url) == LOCKED_URL
    assert client._http.timeout.connect == 10.0


def test_sync_resolve_base_url_is_constant(monkeypatch):
    monkeypatch.setenv("RDB_HANDLER_URL", "http://something-else:1")
    import rdb_manager
    importlib.reload(rdb_manager)
    assert rdb_manager._resolve_base_url() == LOCKED_URL
```

> 주: 이 테스트의 `_reset_modules` fixture는 `rdb_manager.aio`를 import한다. `aio.py`는 Task 5에서 재작성되지만, 현재 구 `aio.py`는 `rdb_handler`(설치돼 있음)를 import하므로 이 단계에서도 import 자체는 성공한다.

- [ ] **Step 3: 테스트 실행해 통과 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_base_url_locked.py -v`
Expected: 동기 테스트 전부 PASS

- [ ] **Step 4: Commit (메시지만 제시)**

```
refactor(rdb-manager): 동기 facade를 HTTP SDK로 재작성 + base_url 잠금
```

---

## Task 5: 비동기 facade `aio.py` 재작성 + base_url 잠금 (비동기)

**Files:**
- Modify: `rdb_manager/aio.py` (전체 재작성)
- Modify: `tests/test_base_url_locked.py` (비동기 테스트 추가)

- [ ] **Step 1: `rdb_manager/aio.py` 전체 재작성**

```python
# -*- coding: utf-8 -*-
"""rdb_manager.aio — 비동기 인터페이스.

sync API와 1:1 대응되는 async API. 함수 이름·시그니처·반환 타입은 sync와
동일, import path로만 구분.

사용법:
    from rdb_manager.aio import fetch_all_dict
    rows = await fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})

    from rdb_manager.aio import execute_dml
    await execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.

Handler 접속 URL은 운영 서버(http://192.7.9.45:8700)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다.
"""

from typing import Any

import pandas as pd

from rdb_manager._client import AsyncClient
from rdb_manager._config import HANDLER_BASE_URL

__all__ = [
    "configure",
    "close",
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    "check_database_connection",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    return HANDLER_BASE_URL


def _get_client() -> AsyncClient:
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


async def close() -> None:
    """SDK 클라이언트 정리."""
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8700으로 고정됩니다.
    timeout만 적용됩니다.
    """
    global _instance
    _instance = AsyncClient(base_url=_resolve_base_url(), timeout=timeout)


# ── SELECT ──────────────────────────────────────────


async def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    return await _get_client().query_to_dataframe(query, params)


async def fetch_all(
    query: str, params: dict[str, Any] | None = None,
) -> list[tuple]:
    return await _get_client().fetch_all(query, params)


async def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None,
) -> list[dict]:
    return await _get_client().fetch_all_dict(query, params)


async def fetch_one(
    query: str, params: dict[str, Any] | None = None,
) -> tuple | None:
    return await _get_client().fetch_one(query, params)


# ── DML ────────────────────────────────────────────


async def execute_dml(
    sql: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_dml(sql, params)


async def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]],
) -> int:
    return await _get_client().execute_many_dml(sql, params_list)


async def execute_insert(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_insert(query, params)


async def execute_update(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_update(query, params)


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
```

- [ ] **Step 2: `tests/test_base_url_locked.py`에 비동기 테스트 추가**

파일 끝에 다음을 추가:

```python
# ── 비동기 ────────────────────────────────────────────


def _get_async_client(env_value, monkeypatch):
    if env_value is None:
        monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    else:
        monkeypatch.setenv("RDB_HANDLER_URL", env_value)

    import rdb_manager.aio as aio
    importlib.reload(aio)
    return aio._get_client()


def test_async_no_env_uses_locked_url(monkeypatch):
    client = _get_async_client(None, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


@pytest.mark.parametrize(
    "wrong_url",
    ["http://wrong-host:1234", "http://localhost:8700", "http://rdb-handler:8700", ""],
)
def test_async_env_is_ignored(monkeypatch, wrong_url):
    client = _get_async_client(wrong_url, monkeypatch)
    assert str(client._http.base_url) == LOCKED_URL


def test_async_configure_base_url_is_ignored(monkeypatch):
    monkeypatch.delenv("RDB_HANDLER_URL", raising=False)
    import rdb_manager.aio as aio
    importlib.reload(aio)

    aio.configure(base_url="http://malicious-host:9999", timeout=12.0)
    client = aio._get_client()
    assert str(client._http.base_url) == LOCKED_URL
    assert client._http.timeout.connect == 12.0


def test_async_resolve_base_url_is_constant(monkeypatch):
    monkeypatch.setenv("RDB_HANDLER_URL", "http://something-else:1")
    import rdb_manager.aio as aio
    importlib.reload(aio)
    assert aio._resolve_base_url() == LOCKED_URL
```

- [ ] **Step 3: 테스트 실행해 통과 확인**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest tests/test_base_url_locked.py -v`
Expected: 동기+비동기 전부 PASS

- [ ] **Step 4: Commit (메시지만 제시)**

```
refactor(rdb-manager): 비동기 facade를 HTTP SDK로 재작성 + base_url 잠금
```

---

## Task 6: 구 모듈 삭제 + pyproject 의존성 교체 + 최종 검증

**Files:**
- Delete: `rdb_manager/query.py`, `rdb_manager/async_query.py`, `rdb_manager/deps.py`, `rdb_manager/async_deps.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: psycopg 직결 구 모듈 삭제**

```bash
cd /home/user/kw/Kiwoom-Manager/rdb-manager
rm rdb_manager/query.py rdb_manager/async_query.py rdb_manager/deps.py rdb_manager/async_deps.py
```

- [ ] **Step 2: `pyproject.toml` 의존성 교체**

`dependencies` 블록을 다음으로 교체 (psycopg·kiwoom-rdb-handler 제거, httpx·pydantic 추가):

```toml
dependencies = [
    "httpx>=0.27.0",
    "pydantic>=2.5.0",
    "pandas>=2.0.0",
]
```

나머지(`[project.optional-dependencies] dev`, `[build-system]`, `[tool.setuptools.packages.find]`, `[tool.pytest.ini_options]`)는 그대로 둔다.

- [ ] **Step 3: 패키지 재설치 (handler/psycopg 의존 제거 반영)**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && pip install -e ".[dev]"`
Expected: 설치 성공, `kiwoom-rdb-handler`/`psycopg`가 더 이상 요구되지 않음

- [ ] **Step 4: import smoke 테스트 — 제거된 심볼 확인용 임시 검증**

Run:
```bash
cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -c "
import rdb_manager, rdb_manager.aio
assert not hasattr(rdb_manager, 'get_connection'), 'get_connection가 아직 노출됨'
assert not hasattr(rdb_manager, 'get_pg_connection'), 'get_pg_connection가 아직 노출됨'
assert not hasattr(rdb_manager, 'PostgresConfig'), 'PostgresConfig가 아직 노출됨'
import importlib.util
assert importlib.util.find_spec('rdb_manager.query') is None, '구 query 모듈이 남아있음'
print('SMOKE OK: HTTP SDK 전환 완료, raw 커넥션 심볼 제거 확인')
"
```
Expected: `SMOKE OK: ...` 출력 (rdb_handler/psycopg 미설치 상태에서도 import 성공)

- [ ] **Step 5: 전체 테스트 실행**

Run: `cd /home/user/kw/Kiwoom-Manager/rdb-manager && python -m pytest -v`
Expected: `test_config_models.py`, `test_sync_client.py`, `test_async_client.py`, `test_base_url_locked.py` 전부 PASS, 실패/에러 0

- [ ] **Step 6: Commit (메시지만 제시)**

```
refactor(rdb-manager): psycopg 직결 모듈 제거 + 의존성을 httpx SDK로 교체
```

---

## Self-Review 결과

**Spec coverage:**
- httpx SDK 1:1 동형 구조 → Task 2/3 (clients), Task 4/5 (facades) ✓
- 패키지 구성(_config/_client/_sync_client/models/exceptions + facade 재작성, 구 모듈 삭제) → Task 1·2·3·4·5·6 ✓
- 공개 API 시그니처 보존 → Task 4/5 facade ✓
- raw 커넥션 심볼 완전 제거 → Task 6 Step 4 smoke로 검증 ✓
- base_url 8700 하드코딩 + override 무시 → Task 1 `_config`, Task 4/5 `test_base_url_locked` ✓
- pyproject 의존성 교체 → Task 6 Step 2 ✓
- 테스트 삭제·대체 (base_url lock + MockTransport 단위) → Task 1·2·3·4·5 ✓
- 검증(pytest 전부 통과 + import smoke + 심볼 제거) → Task 6 ✓
- 비범위(핸들러/서버/머지) → 본 plan에서 제외 ✓

**Placeholder scan:** 모든 코드 스텝에 완전한 코드 포함, TBD/TODO 없음 ✓

**Type consistency:** `SyncClient`/`AsyncClient` 생성자(`base_url`, `timeout`), `_http` 속성, `_get_client`/`_resolve_base_url`/`configure`/`close` 시그니처가 Task 2~5 전반에서 일관 ✓
