# -*- coding: utf-8 -*-
"""Transport-level httpx errors (incl. timeouts) must surface as VDBConnectionError."""

import httpx
import pytest

from vdb_manager.exceptions import VDBConnectionError


def test_sync_client_wraps_read_timeout(monkeypatch):
    from vdb_manager._client import SyncClient

    client = SyncClient()

    def boom(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(client._http, "request", boom)
    with pytest.raises(VDBConnectionError):
        client.get("/v1/admin/status")


def test_sync_client_exposes_every_method_the_sub_clients_use():
    """실제 클라이언트가 다섯 메서드를 갖고 있는지 여기서만 증명된다.

    서브 클라이언트 테스트는 전부 덕 타이핑 더블을 주입하므로, `SyncClient` 에서
    `patch` 를 지워도 그쪽은 전부 초록인 채 실제 호출만 죽는다.
    """
    from vdb_manager._client import SyncClient

    client = SyncClient()

    for name in ("get", "post", "patch", "put", "delete"):
        assert callable(getattr(client, name, None)), f"{name} 없음"


@pytest.mark.parametrize(
    "method,expected",
    [
        ("get", "GET"),
        ("post", "POST"),
        ("patch", "PATCH"),
        ("put", "PUT"),
        ("delete", "DELETE"),
    ],
)
def test_sync_client_sends_the_matching_http_method(monkeypatch, method, expected):
    """메서드 이름과 실제로 나가는 HTTP 메서드가 어긋나지 않는지 본다.

    payload 병합과 전체 교체는 경로와 body 가 같고 HTTP 메서드만 다르므로, 여기서
    `patch` 가 PUT 을 보내면 소비자가 병합을 부르고 전체 교체를 당한다.
    """
    from vdb_manager._client import SyncClient

    client = SyncClient()
    sent = {}

    def capture(http_method, path, **kwargs):
        sent["method"] = http_method
        return httpx.Response(200, json={"ok": True}, request=httpx.Request(http_method, path))

    monkeypatch.setattr(client._http, "request", capture)
    getattr(client, method)("/v1/collections/c/points/payload", json={})

    assert sent["method"] == expected
