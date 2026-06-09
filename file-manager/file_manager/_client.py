# -*- coding: utf-8 -*-
"""httpx 기반 AsyncClient -- FM Service와 통신.

File Manager SDK는 파일 업로드(multipart)와 파일 다운로드(binary)를
다뤄야 하므로 비동기 클라이언트를 사용한다.

base_url(FILE_HANDLER_URL)은 import 시점이 아니라 **첫 요청 시점**에 읽는다.
원격 agent나 llm-request의 런타임 설정(예: ensure_configured)이 import 이후에
env를 세팅하더라도, 첫 HTTP 요청 전에만 잡혀 있으면 정상 동작한다. (모듈 레벨
상수로 동결하면 import 순서에 따라 localhost로 굳는 footgun이 있었음.)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from file_manager.exceptions import FMConnectionError, FMServiceError

DEFAULT_BASE_URL = "http://192.7.9.45:8600"


def _resolve_base_url() -> str:
    """FILE_HANDLER_URL을 호출 시점에 읽는다 (import 시점 동결 방지)."""
    return os.environ.get("FILE_HANDLER_URL", DEFAULT_BASE_URL)


class AsyncClient:
    """FM Service와 통신하는 비동기 HTTP 클라이언트.

    내부 httpx 클라이언트는 첫 요청 시점에 lazy 생성되며, 그때 base_url을 읽는다.
    명시적으로 주소를 고정하고 싶으면 `base_url`을 생성자에 넘길 수 있다(선택적
    override — 넘기면 env보다 우선).
    """

    def __init__(self, timeout: float = 60.0, base_url: str | None = None) -> None:
        self._timeout = timeout
        self._base_url = base_url
        self._http: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """첫 사용 시 httpx 클라이언트를 생성한다. base_url은 이 시점에 결정된다."""
        if self._http is None:
            base = self._base_url or _resolve_base_url()
            self._http = httpx.AsyncClient(base_url=base, timeout=self._timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        client = self._ensure_client()
        try:
            response = await client.request(method, path, **kwargs)
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

    async def post(self, path: str, **kwargs: Any) -> dict:
        """POST 요청 → JSON 응답 반환. `json=` 또는 `files=`/`data=`를 kwargs로 전달."""
        resp = await self._request("POST", path, **kwargs)
        return resp.json()

    async def get_bytes_with_headers(
        self, path: str, **kwargs: Any
    ) -> tuple[bytes, "httpx.Headers"]:
        """GET 요청 → (바이너리 응답, 응답 헤더) 반환.

        파일과 함께 메타데이터를 헤더로 전달받는 엔드포인트용.
        """
        resp = await self._request("GET", path, **kwargs)
        return resp.content, resp.headers
