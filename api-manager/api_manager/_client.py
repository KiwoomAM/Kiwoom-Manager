# -*- coding: utf-8 -*-
"""httpx 기반 HTTP 클라이언트 -- API Handler Service와 통신.

base_url(API_HANDLER_URL)은 import 시점이 아니라 **첫 요청 시점**에 읽는다.
원격 agent나 소비처의 런타임 설정이 import 이후에 env를 세팅하더라도, 첫 HTTP
요청 전에만 잡혀 있으면 정상 동작한다. (모듈 레벨 상수로 동결하면 import 순서에
따라 엉뚱한 주소로 굳는 footgun이 있었음 -- file_manager와 동일한 처리.)
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from api_manager.exceptions import APIManagerConnectionError, APIManagerError

DEFAULT_BASE_URL = "http://192.7.9.45:8400"


def _resolve_base_url() -> str:
    """API_HANDLER_URL을 호출 시점에 읽는다 (import 시점 동결 방지)."""
    return os.environ.get("API_HANDLER_URL", DEFAULT_BASE_URL)


def _parse_error(response: httpx.Response) -> str:
    """에러 응답 body에서 메시지를 추출한다."""
    try:
        return response.json().get("error", response.text)
    except Exception:
        return response.text


class SyncClient:
    """API Handler Service와 통신하는 동기 HTTP 클라이언트.

    내부 httpx 클라이언트는 첫 요청 시점에 lazy 생성되며, 그때 base_url을 읽는다.
    명시적으로 주소를 고정하고 싶으면 `base_url`을 생성자에 넘길 수 있다(선택적
    override -- 넘기면 env보다 우선).
    """

    def __init__(self, timeout: float = 30.0, base_url: str | None = None) -> None:
        self._timeout = timeout
        self._base_url = base_url
        self._http: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        """첫 사용 시 httpx 클라이언트를 생성한다. base_url은 이 시점에 결정된다."""
        if self._http is None:
            base = self._base_url or _resolve_base_url()
            self._http = httpx.Client(base_url=base, timeout=self._timeout)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._ensure_client()
        try:
            response = client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise APIManagerConnectionError(
                f"API Handler Service 연결 실패: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise APIManagerConnectionError(
                f"API Handler Service 응답 타임아웃: {e}"
            ) from e

        if response.status_code >= 400:
            raise APIManagerError(
                _parse_error(response), status_code=response.status_code
            )

        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs).json()


class AsyncClient:
    """API Handler Service와 통신하는 비동기 HTTP 클라이언트.

    내부 httpx 클라이언트는 첫 요청 시점에 lazy 생성되며, 그때 base_url을 읽는다.
    명시적으로 주소를 고정하고 싶으면 `base_url`을 생성자에 넘길 수 있다(선택적
    override -- 넘기면 env보다 우선).
    """

    def __init__(self, timeout: float = 30.0, base_url: str | None = None) -> None:
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

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._ensure_client()
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise APIManagerConnectionError(
                f"API Handler Service 연결 실패: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise APIManagerConnectionError(
                f"API Handler Service 응답 타임아웃: {e}"
            ) from e

        if response.status_code >= 400:
            raise APIManagerError(
                _parse_error(response), status_code=response.status_code
            )

        return response

    async def get(self, path: str, **kwargs: Any) -> Any:
        return (await self._request("GET", path, **kwargs)).json()

    async def post(self, path: str, **kwargs: Any) -> Any:
        return (await self._request("POST", path, **kwargs)).json()
