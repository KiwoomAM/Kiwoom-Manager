# -*- coding: utf-8 -*-
"""httpx 기반 AsyncClient -- MDB Handler Service와 비동기 통신."""

from __future__ import annotations

import os
from typing import Any

import httpx

from mdb_handler.exceptions import MDBConnectionError, MDBHandlerError

BASE_URL = os.environ.get("MDB_HANDLER_URL", "http://192.7.9.45:8300")
DEFAULT_TIMEOUT = 30.0


class AsyncClient:
    """MDB Handler Service와 통신하는 비동기 HTTP 클라이언트."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url or BASE_URL, timeout=timeout
        )
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._http.aclose()
        self._closed = True

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise MDBConnectionError(f"MDB Handler Service 연결 실패: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise MDBHandlerError(error_msg, status_code=response.status_code)

        return response

    async def get(self, path: str, **kwargs: Any) -> Any:
        resp = await self._request("GET", path, **kwargs)
        return resp.json()

    async def post(self, path: str, **kwargs: Any) -> Any:
        resp = await self._request("POST", path, **kwargs)
        return resp.json()

    async def delete(self, path: str, **kwargs: Any) -> Any:
        resp = await self._request("DELETE", path, **kwargs)
        return resp.json()
