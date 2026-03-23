# -*- coding: utf-8 -*-
"""httpx 기반 SyncClient -- MDB Handler Service와 통신."""

from __future__ import annotations

import os
from typing import Any

import httpx

from mdb_handler.exceptions import MDBConnectionError, MDBHandlerError

BASE_URL = os.environ.get("MDB_HANDLER_URL", "http://192.7.9.45:8300")


class SyncClient:
    """MDB Handler Service와 통신하는 동기 HTTP 클라이언트."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
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

    def get(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("GET", path, **kwargs)
        return resp.json()

    def post(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("POST", path, **kwargs)
        return resp.json()

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("DELETE", path, **kwargs)
        return resp.json()
