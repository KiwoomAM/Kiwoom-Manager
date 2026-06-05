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


@pytest.fixture
def mock_manager(monkeypatch):
    """MongoDBManager singleton whose transport records the caller header."""
    monkeypatch.setenv("SERVICE_NAME", "host")
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    mdb = MongoDBManager()
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-caller-service"))
        return httpx.Response(200, json={"inserted_id": "id1", "data": []})

    mdb._client._http = httpx.Client(
        base_url="http://test",
        headers=mdb._client._http.headers,
        transport=httpx.MockTransport(handler),
    )
    yield mdb, seen
    MongoDBManager.reset()


def test_with_caller_overrides_then_default_does_not_leak(mock_manager):
    mdb, seen = mock_manager
    mdb.with_caller("llm-manager").insert_one({"x": 1})   # llm-manager
    mdb.insert_one({"x": 1})                               # host — must NOT leak
    assert seen == ["llm-manager", "host"]


def test_with_caller_returns_new_object_not_singleton(mock_manager):
    mdb, _ = mock_manager
    assert mdb.with_caller("llm-manager") is not mdb


def test_bound_envelope_getter_inherits_caller(mock_manager):
    mdb, seen = mock_manager
    mdb.with_caller("llm-manager").get_agent_output().find({"a": 1})
    assert seen == ["llm-manager"]


def test_two_bound_handles_are_isolated(mock_manager):
    mdb, seen = mock_manager
    a = mdb.with_caller("svc-a")
    b = mdb.with_caller("svc-b")
    a.insert_one({"x": 1})
    b.insert_one({"x": 1})
    a.find()
    assert seen == ["svc-a", "svc-b", "svc-a"]
