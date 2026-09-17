# -*- coding: utf-8 -*-
"""httpx 기반 SyncClient -- VDB Handler Service와 통신"""

from __future__ import annotations

import os
from typing import Any

import httpx

from vdb_manager.exceptions import VDBConnectionError, VDBHandlerError

BASE_URL = os.environ.get("VDB_HANDLER_URL", "http://192.7.9.45:8500")


def _error_message(response: httpx.Response) -> str:
    """에러 응답에서 사람이 읽을 메시지를 뽑는다.

    Service 는 두 가지 모양으로 답한다. 자체 예외는 `{"error": "..."}` 로 나가지만,
    요청 검증 실패(모르는 키·id 형식·길이 불일치)는 FastAPI 기본인
    `{"detail": [{"loc": [...], "msg": "..."}]}` 로 나간다. 앞의 것만 읽으면 뒤의
    경우 날것의 JSON 덩어리가 그대로 예외 메시지가 된다.

    Args:
        response: 4xx/5xx 응답.

    Returns:
        한 줄로 정리한 메시지. 형식을 알아볼 수 없으면 본문 텍스트 그대로.
    """
    try:
        body = response.json()
    except Exception:
        return response.text

    if not isinstance(body, dict):
        return response.text

    if "error" in body:
        return str(body["error"])

    detail = body.get("detail")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            location = ".".join(str(x) for x in item.get("loc", []))
            message = str(item.get("msg", item))
            parts.append(f"{location}: {message}" if location else message)
        if parts:
            return "; ".join(parts)
    if detail is not None:
        return str(detail)

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

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._request("DELETE", path, **kwargs)
        return resp.json()
