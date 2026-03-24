# -*- coding: utf-8 -*-
"""httpx 기반 HTTP 클라이언트 -- API Handler Service와 통신."""

from __future__ import annotations

import os
from typing import Any

import httpx

from api_handler_sdk.exceptions import APIHandlerConnectionError, APIHandlerSDKError

BASE_URL = os.environ.get("API_HANDLER_URL", "http://192.7.9.45:8400")


class SyncClient:
    """API Handler Service와 통신하는 동기 HTTP 클라이언트."""

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
            raise APIHandlerConnectionError(
                f"API Handler Service 연결 실패: {e}"
            ) from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise APIHandlerSDKError(error_msg, status_code=response.status_code)

        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs).json()


class AsyncClient:
    """API Handler Service와 통신하는 비동기 HTTP 클라이언트."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise APIHandlerConnectionError(
                f"API Handler Service 연결 실패: {e}"
            ) from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise APIHandlerSDKError(error_msg, status_code=response.status_code)

        return response

    async def get(self, path: str, **kwargs: Any) -> Any:
        return (await self._request("GET", path, **kwargs)).json()

    async def post(self, path: str, **kwargs: Any) -> Any:
        return (await self._request("POST", path, **kwargs)).json()
