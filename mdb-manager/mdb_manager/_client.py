# -*- coding: utf-8 -*-
"""httpx 기반 SyncClient -- MDB Handler Service와 통신."""

from __future__ import annotations

import os
from typing import Any

import httpx

from mdb_manager.exceptions import (
    MDBConfigError,
    MDBConnectionError,
    MDBHandlerError,
)

BASE_URL = os.environ.get("MDB_HANDLER_URL", "http://192.7.9.45:8300")


def _default_headers() -> dict[str, str]:
    """SERVICE_NAME을 X-Caller-Service 헤더로 부착한다.

    mdb-manager는 caller 식별이 필요한 작업(write / envelope read·write)을
    위해 SERVICE_NAME을 요구한다. 미설정 시 클라이언트 생성 시점에 즉시
    MDBConfigError를 던져 fail-fast 한다 (서버측 400을 첫 요청까지 기다리지 않음).
    """
    service_name = os.environ.get("SERVICE_NAME")
    if not service_name:
        raise MDBConfigError(
            "SERVICE_NAME 환경변수가 설정되지 않았습니다. "
            "mdb-manager는 caller 식별을 위해 SERVICE_NAME이 필요합니다. "
            "호출 서비스에 SERVICE_NAME=<your-service>를 설정하세요 "
            "(예: .env에 SERVICE_NAME=kiwoom-js 추가 후 load_dotenv())."
        )
    return {"X-Caller-Service": service_name}


class SyncClient:
    """MDB Handler Service와 통신하는 동기 HTTP 클라이언트."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers=_default_headers(),
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
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
