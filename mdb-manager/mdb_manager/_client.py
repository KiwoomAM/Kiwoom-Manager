# -*- coding: utf-8 -*-
"""httpx 기반 SyncClient -- MDB Handler Service와 통신."""

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


def _default_headers(auth_key: str | None = None) -> dict[str, str]:
    """요청마다 싣는 기본 헤더 — 접근 키와, SERVICE_NAME 이 있으면 X-Caller-Service.

    접근 키는 핸들러의 접근 등급을 정한다(레포 루트 CONTEXT.md 「auth_key」). 주지 않으면(None) admin 키를
    싣고, 빈 문자열은 그대로 실어 핸들러가 401 로 거절한다 — 비어 있는 키가 조용히 admin 이 되지 않게.

    SERVICE_NAME 미설정 시 X-Caller-Service 없이 통과한다 (클라이언트 생성은 항상 성공).
    caller가 실제로 필요한 envelope 진입점(get_agent_data/get_agent_output)에서만
    fail-fast 하므로, blocks read 등 caller 불필요 작업은 SERVICE_NAME 없이 동작한다.
    """
    headers = {"Authorization": f"Bearer {_ADMIN_KEY if auth_key is None else auth_key}"}
    service_name = os.environ.get("SERVICE_NAME")
    if service_name:
        headers["X-Caller-Service"] = service_name
    return headers


class SyncClient:
    """MDB Handler Service와 통신하는 동기 HTTP 클라이언트."""

    def __init__(self, timeout: float = 30.0, auth_key: str | None = None) -> None:
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers=_default_headers(auth_key),
        )

    def close(self) -> None:
        self._http.close()

    @property
    def caller_identity(self) -> str | None:
        """현재 클라이언트의 기본 caller(X-Caller-Service). 미설정 시 None."""
        return self._http.headers.get("X-Caller-Service")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        caller = kwargs.pop("caller", None)
        if caller:
            headers = dict(kwargs.pop("headers", None) or {})
            headers["X-Caller-Service"] = caller
            kwargs["headers"] = headers
        try:
            response = self._http.request(method, path, **kwargs)
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

    def get(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("GET", path, **kwargs)
        return resp.json()

    def post(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("POST", path, **kwargs)
        return resp.json()

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("DELETE", path, **kwargs)
        return resp.json()


class _CallerBoundClient:
    """SyncClient를 감싸 모든 요청에 caller(X-Caller-Service)를 주입하는 패스스루.

    내부 httpx 클라이언트/커넥션 풀을 공유하며 lifecycle(close)은 소유하지 않는다.
    정체성은 요청마다 caller 인자로만 흐르고 공유 상태에 저장되지 않는다.
    """

    def __init__(self, base: "SyncClient", caller: str) -> None:
        self._base = base
        self._caller = caller

    @property
    def caller_identity(self) -> str | None:
        return self._caller

    def get(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return self._base.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return self._base.post(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        kwargs.setdefault("caller", self._caller)
        return self._base.delete(path, **kwargs)
