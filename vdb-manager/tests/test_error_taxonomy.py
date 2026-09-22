# -*- coding: utf-8 -*-
"""오류가 어떤 갈래로 올라오는가.

게이트웨이는 "닿지 못했다"(503)와 "닿았는데 실패했다"(500)를 구분해 답한다. 앞은
재시도가 의미 있고 뒤는 없다 — 그 구분을 만든 이유가 그것이다
(`KiwoomAM/Kiwoom-Handler` 의 ADR 0003).

SDK 가 둘을 같은 예외로 올리면 그 구분이 숫자로만 남는다. 호출자가 상태 코드를 읽어
갈라 쓸 수는 있지만, 그것은 갈라 쓸 수단이 그것뿐이어서다.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vdb_manager._client import SyncClient
from vdb_manager.exceptions import (
    VDBConnectionError,
    VDBError,
    VDBHandlerError,
    VDBUnavailableError,
)


class _Transport(httpx.BaseTransport):
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.body, (dict, list)):
            return httpx.Response(self.status, json=self.body, request=request)
        return httpx.Response(self.status, text=self.body, request=request)


def _client(status: int, body: Any) -> SyncClient:
    client = SyncClient()
    client._http = httpx.Client(base_url="http://test", transport=_Transport(status, body))
    return client


def test_닿지_못하면_전용_예외로_올린다():
    with pytest.raises(VDBUnavailableError) as caught:
        _client(503, {"error": "count 실패: 연결 거부"}).get("/v1/admin/status")

    assert caught.value.status_code == 503


def test_그_예외를_기존_except_가_그대로_잡는다():
    """새 갈래를 더하면서 기존 호출자를 깨지 않는다.

    `VDBHandlerError` 를 상속하므로 `except VDBHandlerError` 는 전과 같이 잡고
    `status_code` 도 그대로 남는다.
    """
    with pytest.raises(VDBHandlerError):
        _client(503, {"error": "x"}).get("/v1/admin/status")

    with pytest.raises(VDBError):
        _client(503, {"error": "x"}).get("/v1/admin/status")


def test_닿았는데_실패한_것은_전용_예외가_아니다():
    """500 은 재시도해도 같은 답이 온다. 503 과 같은 갈래로 묶으면 구분이 사라진다."""
    with pytest.raises(VDBHandlerError) as caught:
        _client(500, {"error": "내부 오류"}).post("/v1/collections/c/count")

    assert not isinstance(caught.value, VDBUnavailableError)
    assert caught.value.status_code == 500


def test_호출자_잘못은_전용_예외가_아니다():
    with pytest.raises(VDBHandlerError) as caught:
        _client(422, {"error": "body.ids.0: UUID 아님"}).post("/v1/collections/c/points")

    assert not isinstance(caught.value, VDBUnavailableError)
    assert caught.value.status_code == 422


def test_연결_자체가_안_되면_다른_갈래다():
    """서비스에 닿지도 못한 것은 서버가 503 을 준 것과 다르다."""
    client = SyncClient()
    client._http = httpx.Client(base_url="http://test", transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
    ))

    with pytest.raises(VDBConnectionError) as caught:
        client.get("/v1/admin/status")

    assert caught.value.status_code == 0
    assert not isinstance(caught.value, VDBHandlerError)
