# -*- coding: utf-8 -*-
"""httpx 기반 SyncClient -- VDB Handler Service와 통신"""

from __future__ import annotations

import os
from typing import Any

import httpx

from vdb_manager.exceptions import (
    VDBConnectionError,
    VDBHandlerError,
    VDBUnavailableError,
)

# 저장소에 닿지 못했다는 뜻. 재시도가 의미 있는 유일한 상태 코드다.
_UNAVAILABLE = 503

BASE_URL = os.environ.get("VDB_HANDLER_URL", "http://192.7.9.45:8500")


def _error_message(response: httpx.Response) -> str:
    """에러 응답에서 사람이 읽을 메시지를 뽑는다.

    Service 는 자체 예외든 요청 검증 실패든 `{"error": "..."}` 하나로 답한다. 위치가
    필요한 검증 실패는 서버가 `body.ids.0: ...` 꼴로 앞에 붙여 주고 여러 건은 ` / ` 로
    이어 보내므로, 여기서 다시 조립할 것이 없다.

    전에는 검증 실패가 FastAPI 기본인 `{"detail": [...]}` 로 나가 SDK 가 그것을
    평탄화했다. 서버가 한 모양으로 정규화하면서 그 경로는 닿지 않게 됐고, 닿지 않는
    코드를 남겨 두면 다음 사람이 그것을 서버 계약으로 읽는다.

    Args:
        response: 4xx/5xx 응답.

    Returns:
        서버가 준 한 줄. 형식을 알아볼 수 없으면 본문 텍스트 그대로.
    """
    try:
        body = response.json()
    except Exception:
        return response.text

    if not isinstance(body, dict):
        return response.text

    if "error" in body:
        return str(body["error"])

    return response.text


class SyncClient:
    """VDB Handler Service와 통신하는 동기 HTTP 클라이언트"""

    def __init__(self, timeout: float = 120.0) -> None:
        self._http = httpx.Client(base_url=BASE_URL, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise VDBConnectionError(
                f"VDB Handler Service 연결 실패: {e}"
            ) from e

        if response.status_code == _UNAVAILABLE:
            raise VDBUnavailableError(_error_message(response))

        if response.status_code >= 400:
            raise VDBHandlerError(
                _error_message(response), status_code=response.status_code
            )

        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("GET", path, **kwargs)
        return resp.json()

    def post(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("POST", path, **kwargs)
        return resp.json()

    def patch(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("PATCH", path, **kwargs)
        return resp.json()

    def put(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("PUT", path, **kwargs)
        return resp.json()

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("DELETE", path, **kwargs)
        return resp.json()
