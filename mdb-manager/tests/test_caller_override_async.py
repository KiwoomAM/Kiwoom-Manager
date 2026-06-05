# -*- coding: utf-8 -*-
"""Per-request caller override + _AsyncCallerBoundClient (async)."""

import httpx
import pytest


@pytest.fixture
def mock_aclient(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "host")
    from mdb_manager._async_client import AsyncClient

    client = AsyncClient()
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-caller-service"))
        return httpx.Response(200, json={"ok": True})

    client._http = httpx.AsyncClient(
        base_url="http://test",
        headers=client._http.headers,
        transport=httpx.MockTransport(handler),
    )
    return client, seen


async def test_async_caller_kwarg_overrides_default(mock_aclient):
    client, seen = mock_aclient
    await client.post("/p", json={}, caller="llm-manager")
    await client.post("/p", json={})
    assert seen == ["llm-manager", "host"]


async def test_async_bound_client_injects_caller(mock_aclient):
    from mdb_manager._async_client import _AsyncCallerBoundClient

    client, seen = mock_aclient
    bound = _AsyncCallerBoundClient(client, "llm-manager")
    await bound.post("/p", json={})
    await client.post("/p", json={})
    assert seen == ["llm-manager", "host"]
