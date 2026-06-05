# -*- coding: utf-8 -*-
"""Per-request caller override + _CallerBoundClient (sync)."""

import httpx
import pytest


@pytest.fixture
def mock_client(monkeypatch):
    """SyncClient whose transport records the final X-Caller-Service header."""
    monkeypatch.setenv("SERVICE_NAME", "host")
    from mdb_manager._client import SyncClient

    client = SyncClient()
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-caller-service"))
        return httpx.Response(200, json={"ok": True})

    client._http = httpx.Client(
        base_url="http://test",
        headers=client._http.headers,
        transport=httpx.MockTransport(handler),
    )
    return client, seen


def test_caller_kwarg_overrides_default_header(mock_client):
    client, seen = mock_client
    client.post("/p", json={}, caller="llm-manager")
    client.post("/p", json={})
    assert seen == ["llm-manager", "host"]


def test_bound_client_injects_caller(mock_client):
    from mdb_manager._client import _CallerBoundClient

    client, seen = mock_client
    bound = _CallerBoundClient(client, "llm-manager")
    bound.post("/p", json={})
    client.post("/p", json={})
    assert seen == ["llm-manager", "host"]
