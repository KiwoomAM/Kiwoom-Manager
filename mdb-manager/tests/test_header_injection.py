# -*- coding: utf-8 -*-
"""SERVICE_NAME 가드 + X-Caller-Service 헤더 자동 부착 검증.

mdb-manager는 caller 식별이 필요한 표준 에이전트 래퍼이므로, SERVICE_NAME이
설정되지 않으면 클라이언트/핸들러 생성 시점에 즉시 MDBConfigError로 실패한다
(서버측 400을 첫 요청까지 기다리지 않고 fail-fast).
"""

import importlib

import pytest

from mdb_manager.exceptions import MDBConfigError


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
# SERVICE_NAME 설정 시 — 헤더 부착
# ---------------------------------------------------------------------------


def test_sync_client_attaches_header_when_service_name_set(monkeypatch, reload_clients):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._client import SyncClient

    client = SyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"


def test_async_client_attaches_header_when_service_name_set(
    monkeypatch, reload_clients
):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._async_client import AsyncClient

    client = AsyncClient()
    assert client._http.headers.get("X-Caller-Service") == "test-agent"


# ---------------------------------------------------------------------------
# SERVICE_NAME 미설정 시 — 생성 시점 hard-fail
# ---------------------------------------------------------------------------


def test_sync_client_raises_when_service_name_unset(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._client import SyncClient

    with pytest.raises(MDBConfigError, match="SERVICE_NAME"):
        SyncClient()


def test_async_client_raises_when_service_name_unset(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager._async_client import AsyncClient

    with pytest.raises(MDBConfigError, match="SERVICE_NAME"):
        AsyncClient()


def test_sync_handler_raises_when_service_name_unset(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    with pytest.raises(MDBConfigError, match="SERVICE_NAME"):
        MongoDBManager()


def test_async_handler_raises_when_service_name_unset(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.aio import AsyncMongoDBManager

    with pytest.raises(MDBConfigError, match="SERVICE_NAME"):
        AsyncMongoDBManager()


def test_sync_handler_not_poisoned_after_failed_construction(
    monkeypatch, reload_clients
):
    """미설정으로 실패한 직후에도 SERVICE_NAME을 채우면 정상 생성돼야 한다."""
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    with pytest.raises(MDBConfigError):
        MongoDBManager()

    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    handler = MongoDBManager()
    assert handler._client._http.headers.get("X-Caller-Service") == "test-agent"
    MongoDBManager.reset()
