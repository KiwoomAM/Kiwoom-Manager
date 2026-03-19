"""AsyncClient — 비동기 HTTP 클라이언트."""

import logging
from typing import Any

import httpx
import pandas as pd

from winus_handler.exceptions import HandlerConnectionError, HandlerError

logger = logging.getLogger(__name__)
from winus_handler.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)


class AsyncClient:
    """WINUS Handler 서비스와 통신하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as e:
            raise HandlerConnectionError(f"Handler 연결 실패: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                detail = body.get("detail")
            except Exception:
                error_msg = response.text
                detail = None
            raise HandlerError(error_msg, status_code=response.status_code, detail=detail)

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
