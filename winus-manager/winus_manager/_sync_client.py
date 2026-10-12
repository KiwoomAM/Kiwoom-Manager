"""SyncClient — 동기 HTTP 클라이언트."""

import logging
from typing import Any

import httpx
import pandas as pd

from winus_manager.exceptions import (
    QueryExecutionException,
)
from winus_manager._wire import (
    access_headers,
    log_check_failure,
    dml_body,
    dml_many_body,
    ExecutionOptions,
    error_from_response,
    error_from_transport,
    query_body,
)
from winus_manager.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)


logger = logging.getLogger(__name__)


class SyncClient:
    """WINUS Handler 서비스와 통신하는 동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 30.0, auth_key: str | None = None) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout, headers=access_headers(auth_key))

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, *,
        options: ExecutionOptions = ExecutionOptions(), **kwargs: object,
    ) -> httpx.Response:
        """공통 HTTP 요청 처리. 전송 실패는 조회가 돌았을 수 있는지로 구분해 올린다."""
        try:
            response = self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise error_from_transport(e, options) from e

        if response.status_code >= 400:
            raise error_from_response(response)

        return response

    def _options(
        self, client_query_id: str | None, query_timeout_ms: int | None,
    ) -> ExecutionOptions:
        """조회마다 붙이는 실행 옵션. 마감이 이 클라이언트의 소비자 타임아웃 안에 드는지 확인한다."""
        options = ExecutionOptions(client_query_id=client_query_id, query_timeout_ms=query_timeout_ms)
        options.check_within(self._http.timeout.read)
        return options

    # ── SELECT ──────────────────────────────────────────

    def query_to_dataframe(
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> pd.DataFrame:
        """SELECT → pandas DataFrame."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/query/dataframe", json=query_body(query, params, options), options=options)
        data = DataFrameData.model_validate(resp.json())
        df = pd.DataFrame(data.rows, columns=data.columns)
        # dtype 복원
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
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> list[tuple]:
        """SELECT → list of tuples."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/query/all", json=query_body(query, params, options), options=options)
        data = RowsData.model_validate(resp.json())
        return [tuple(row) for row in data.rows]

    def fetch_all_dict(
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> list[dict]:
        """SELECT → list of dicts."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/query/all-dict", json=query_body(query, params, options), options=options)
        data = DictRowsData.model_validate(resp.json())
        return data.rows

    def fetch_one(
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> tuple | None:
        """SELECT → 첫 번째 row 또는 None."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/query/one", json=query_body(query, params, options), options=options)
        data = OneRowData.model_validate(resp.json())
        return tuple(data.row) if data.row is not None else None

    # ── DML ────────────────────────────────────────────

    def execute_dml(
        self, sql: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> int:
        """일반 DML 실행."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/dml/execute", json=dml_body(sql, params, options), options=options)
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_many_dml(
        self, sql: str, params_list: list[dict[str, Any]], *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> int:
        """배치 DML 실행."""
        options = self._options(client_query_id, query_timeout_ms)
        if not params_list:
            return 0
        resp = self._request(
            "POST", "/v1/dml/execute-many",
            json=dml_many_body(sql, params_list, options), options=options,
        )
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_insert(
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> int:
        """INSERT 전용."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/dml/insert", json=dml_body(query, params, options), options=options)
        return DmlResult.model_validate(resp.json()).rowcount

    def execute_update(
        self, query: str, params: dict[str, Any] | None = None, *,
        client_query_id: str | None = None, query_timeout_ms: int | None = None,
    ) -> int:
        """UPDATE 전용."""
        options = self._options(client_query_id, query_timeout_ms)
        resp = self._request("POST", "/v1/dml/update", json=dml_body(query, params, options), options=options)
        return DmlResult.model_validate(resp.json()).rowcount

    # ── Health ─────────────────────────────────────────

    def check_database_connection(self) -> bool:
        """DB 연결 상태 확인."""
        try:
            resp = self._request("GET", "/v1/check")
            return resp.json().get("connected", False)
        except Exception as e:
            log_check_failure(e)
            return False
