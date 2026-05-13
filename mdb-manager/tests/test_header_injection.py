# -*- coding: utf-8 -*-
"""SyncClient/AsyncClient의 X-Caller-Service 헤더 자동 부착 검증."""

import importlib
import pytest


@pytest.fixture
def reload_clients():
    """env 변경 후 SDK 모듈을 reimport해서 BASE_URL/캐시 초기화."""
    import mdb_handler._client as sync_mod
    import mdb_handler._async_client as async_mod
    importlib.reload(sync_mod)
    importlib.reload(async_mod)
    yield
    importlib.reload(sync_mod)
    importlib.reload(async_mod)


def test_sync_client_attaches_header_when_service_name_set(
    monkeypatch, reload_clients
):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_handler._client import SyncClient

    client = SyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"


def test_sync_client_omits_header_when_service_name_unset(
    monkeypatch, reload_clients
):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_handler._client import SyncClient

    client = SyncClient()
    assert "X-Caller-Service" not in client._http.headers


def test_async_client_attaches_header_when_service_name_set(
    monkeypatch, reload_clients
):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_handler._async_client import AsyncClient

    client = AsyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"


def test_async_client_omits_header_when_service_name_unset(
    monkeypatch, reload_clients
):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_handler._async_client import AsyncClient

    client = AsyncClient()
    assert "X-Caller-Service" not in client._http.headers
