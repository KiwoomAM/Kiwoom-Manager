# -*- coding: utf-8 -*-
"""httpx 기반 AsyncClient -- MDB Handler Service와 비동기 통신."""

from __future__ import annotations

import os
from typing import Any

import httpx

from mdb_manager._config import _ADMIN_KEY
from mdb_manager.exceptions import (
    MDBConnectionError,
    MDBHandlerError,
)

BASE_URL = os.environ.get("MDB_HANDLER_URL", "http://192.7.9.45:8300")
DEFAULT_TIMEOUT = 30.0


def _default_headers(auth_key: str | None = None) -> dict[str, str]:
    """요청마다 싣는 기본 헤더 — 접근 키와, SERVICE_NAME 이 있으면 X-Caller-Service.

    접근 키는 핸들러의 접근 등급을 정한다(레포 루트 CONTEXT.md 「auth_key」). 비어 있으면 admin 키를 싣는다.

    SERVICE_NAME 미설정 시 X-Caller-Service 없이 통과한다 (클라이언트 생성은 항상 성공).
    caller가 실제로 필요한 envelope 진입점(get_agent_data/get_agent_output)에서만
    fail-fast 하므로, blocks read 등 caller 불필요 작업은 SERVICE_NAME 없이 동작한다.
    """
    headers = {"Authorization": f"Bearer {auth_key or _ADMIN_KEY}"}
    service_name = os.environ.get("SERVICE_NAME")
    if service_name:
        headers["X-Caller-Service"] = service_name
    return headers


class AsyncClient:
    """MDB Handler Service와 통신하는 비동기 HTTP 클라이언트."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        auth_key: str | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url or BASE_URL,
            timeout=timeout,
            headers=_default_headers(auth_key),
        )
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._http.aclose()
        self._closed = True

    @property
    def caller_identity(self) -> str | None:
        """현재 클라이언트의 기본 caller(X-Caller-Service). 미설정 시 None."""
        return self._http.headers.get("X-Caller-Service")

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        caller = kwargs.pop("caller", None)
        if caller:
            headers = dict(kwargs.pop("headers", None) or {})
            headers["X-Caller-Service"] = caller
            kwargs["headers"] = headers
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as e:
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


class _AsyncCallerBoundClient:
    """AsyncClient를 감싸 모든 요청에 caller(X-Caller-Service)를 주입하는 패스스루.

    내부 httpx 클라이언트를 공유하며 lifecycle(aclose)은 소유하지 않는다.
    정체성은 요청마다 caller 인자로만 흐르고 공유 상태에 저장되지 않는다.
    """

    def __init__(self, base: "AsyncClient", caller: str) -> None:
        self._base = base
        self._caller = caller

    @property
    def caller_identity(self) -> str | None:
        return self._caller

    async def get(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return await self._base.get(path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return await self._base.post(path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return await self._base.delete(path, **kwargs)
