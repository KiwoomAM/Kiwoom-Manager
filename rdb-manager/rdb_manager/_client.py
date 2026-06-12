"""AsyncClient — 비동기 HTTP 클라이언트."""

import logging
from typing import Any

import httpx
import pandas as pd

from rdb_manager.exceptions import HandlerConnectionError, HandlerError, SDKError
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
        except httpx.TimeoutException as e:
            raise HandlerConnectionError(f"[RDB 핸들러] 요청 시간 초과: {e}") from e
        except httpx.TransportError as e:  # ConnectError 포함 그 외 전송 계층 오류
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

    async def query_to_dataframe(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """SELECT → pandas DataFrame."""
        resp = await self._request("POST", "/v1/query/dataframe", json=self._query_body(query, params))
        data = DataFrameData.model_validate(resp.json())
        df = pd.DataFrame(data.rows, columns=data.columns)
        for col, dtype in data.dtypes.items():
            if col not in df.columns:
                continue
            try:
                if dtype == "datetime64[ns]":
                    df[col] = pd.to_datetime(df[col]).astype("datetime64[ns]")
                elif dtype in ("int64", "float64"):
                    df[col] = pd.to_numeric(df[col])
            except Exception as e:
                logger.warning("컬럼 '%s' dtype 복원 실패 (%s): %s", col, dtype, e)
        return df

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[tuple]:
        """SELECT → list of tuples."""
        resp = await self._request("POST", "/v1/query/all", json=self._query_body(query, params))
        data = RowsData.model_validate(resp.json())
        return [tuple(row) for row in data.rows]

    async def fetch_all_dict(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> list[dict]:
        """SELECT → list of dicts."""
        resp = await self._request("POST", "/v1/query/all-dict", json=self._query_body(query, params))
        data = DictRowsData.model_validate(resp.json())
        return data.rows

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> tuple | None:
        """SELECT → 첫 번째 row 또는 None."""
        resp = await self._request("POST", "/v1/query/one", json=self._query_body(query, params))
        data = OneRowData.model_validate(resp.json())
        return tuple(data.row) if data.row is not None else None

    # ── DML ────────────────────────────────────────────

    async def execute_dml(
        self, sql: str, params: dict[str, Any] | None = None,
    ) -> int:
        """일반 DML 실행."""
        resp = await self._request("POST", "/v1/dml/execute", json=self._dml_body(sql, params))
        return DmlResult.model_validate(resp.json()).rowcount

    async def execute_many_dml(
        self, sql: str, params_list: list[dict[str, Any]],
    ) -> int:
        """배치 DML 실행."""
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
        """INSERT 전용."""
        resp = await self._request("POST", "/v1/dml/insert", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    async def execute_update(
        self, query: str, params: dict[str, Any] | None = None,
    ) -> int:
        """UPDATE 전용."""
        resp = await self._request("POST", "/v1/dml/update", json=self._dml_body(query, params))
        return DmlResult.model_validate(resp.json()).rowcount

    # ── Health ─────────────────────────────────────────

    async def check_database_connection(self) -> bool:
        """DB 연결 상태 확인."""
        try:
            resp = await self._request("GET", "/v1/check")
            return resp.json().get("connected", False)
        except (SDKError, httpx.HTTPError) as e:
            logger.warning("[RDB 핸들러] DB 연결 확인 실패: %s", e)
            return False
