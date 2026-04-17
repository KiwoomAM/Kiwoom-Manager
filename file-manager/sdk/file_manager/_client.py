# -*- coding: utf-8 -*-
"""httpx 기반 AsyncClient -- FM Service와 통신.

File Manager SDK는 파일 업로드(multipart)와 파일 다운로드(binary)를
다뤄야 하므로 비동기 클라이언트를 사용한다.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from file_manager.exceptions import FMConnectionError, FMServiceError

BASE_URL = os.environ.get("FILE_MANAGER_URL", "http://localhost:8600")


class AsyncClient:
    """FM Service와 통신하는 비동기 HTTP 클라이언트."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise FMConnectionError(
                f"File Manager Service 연결 실패: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise FMConnectionError(
                f"File Manager Service 응답 타임아웃: {e}"
            ) from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
            except Exception:
                error_msg = response.text
            raise FMServiceError(error_msg, status_code=response.status_code)

        return response

    async def post_json(self, path: str, **kwargs: Any) -> dict:
        """JSON body POST 요청 → JSON 응답 반환."""
        resp = await self._request("POST", path, **kwargs)
        return resp.json()

    async def post_multipart(self, path: str, **kwargs: Any) -> dict:
        """Multipart POST 요청 → JSON 응답 반환."""
        resp = await self._request("POST", path, **kwargs)
        return resp.json()

    async def get_bytes(self, path: str, **kwargs: Any) -> bytes:
        """GET 요청 → 바이너리 응답 반환."""
        resp = await self._request("GET", path, **kwargs)
        return resp.content
