# -*- coding: utf-8 -*-
"""SERVICE_NAME → X-Caller-Service 헤더 부착 + caller_identity 검증.

mdb-manager는 SERVICE_NAME이 있으면 X-Caller-Service 헤더를 자동 부착한다.
SERVICE_NAME이 없어도 클라이언트/핸들러 생성은 항상 성공한다 (생성 시점 hard-fail 없음).
caller가 실제로 필요한 envelope 진입점에서만 fail-fast 하며, 그 검증은
test_envelope_guard.py가 담당한다.
"""

import importlib

import pytest


@pytest.fixture
def reload_clients():
    """env 변경 후 SDK 모듈을 reimport해서 BASE_URL/캐시 초기화."""
    import mdb_manager._client as sync_mod
    import mdb_manager._async_client as async_mod

    importlib.reload(sync_mod)
    importlib.reload(async_mod)
    yield
    importlib.reload(sync_mod)
    importlib.reload(async_mod)


# ---------------------------------------------------------------------------
# SERVICE_NAME 설정 시 — 헤더 부착 + caller_identity 노출
# ---------------------------------------------------------------------------


def test_sync_client_attaches_header_when_service_name_set(monkeypatch, reload_clients):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._client import SyncClient

    client = SyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"
    assert client.caller_identity == "test-agent"


def test_async_client_attaches_header_when_service_name_set(
    monkeypatch, reload_clients
):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._async_client import AsyncClient

    client = AsyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"
    assert client.caller_identity == "test-agent"


# ---------------------------------------------------------------------------
# SERVICE_NAME 미설정 시 — 생성 성공, 헤더 없음, caller_identity None
# ---------------------------------------------------------------------------


def test_sync_client_succeeds_without_service_name(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._client import SyncClient

    client = SyncClient()
    assert client._http.headers.get("X-Caller-Service") is None
    assert client.caller_identity is None


def test_async_client_succeeds_without_service_name(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._async_client import AsyncClient

    client = AsyncClient()
    assert client._http.headers.get("X-Caller-Service") is None
    assert client.caller_identity is None


def test_sync_handler_succeeds_without_service_name(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    manager = MongoDBManager()
    assert manager._client.caller_identity is None
    MongoDBManager.reset()


async def test_async_handler_succeeds_without_service_name(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.aio import AsyncMongoDBManager

    manager = AsyncMongoDBManager()
    assert manager._client.caller_identity is None
    await manager.aclose()


# ---------------------------------------------------------------------------
# bound client — caller_identity는 bound caller를 반환
# ---------------------------------------------------------------------------


def test_sync_bound_client_caller_identity(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._client import SyncClient, _CallerBoundClient

    bound = _CallerBoundClient(SyncClient(), "llm-manager")
    assert bound.caller_identity == "llm-manager"


def test_async_bound_client_caller_identity(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._async_client import AsyncClient, _AsyncCallerBoundClient

    bound = _AsyncCallerBoundClient(AsyncClient(), "llm-manager")
    assert bound.caller_identity == "llm-manager"
