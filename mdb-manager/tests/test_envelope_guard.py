# -*- coding: utf-8 -*-
"""envelope 진입점 caller 가드 검증.

SERVICE_NAME(또는 with_caller caller)이 없으면 get_agent_data/get_agent_output이
MDBConfigError로 fail-fast 한다. blocks 진입점(get_collection 등)은 가드 없이
통과한다. caller가 있으면 envelope 진입점도 통과한다. 모든 검증은 네트워크 호출
이전에 일어나므로 실제 서비스 없이 테스트 가능하다.
"""

import importlib

import pytest

from mdb_manager.exceptions import MDBConfigError


@pytest.fixture
def reload_clients():
    import mdb_manager._client as sync_mod
    import mdb_manager._async_client as async_mod

    importlib.reload(sync_mod)
    importlib.reload(async_mod)
    yield
    importlib.reload(sync_mod)
    importlib.reload(async_mod)


# caller 부재 → envelope 진입 시 raise

def test_sync_get_agent_data_raises_without_caller(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    mdb = MongoDBManager()
    with pytest.raises(MDBConfigError, match="caller"):
        mdb.get_agent_data()
    with pytest.raises(MDBConfigError, match="caller"):
        mdb.get_agent_output()
    MongoDBManager.reset()


async def test_async_get_agent_data_raises_without_caller(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.aio import AsyncMongoDBManager

    mdb = AsyncMongoDBManager()
    with pytest.raises(MDBConfigError, match="caller"):
        mdb.get_agent_data()
    with pytest.raises(MDBConfigError, match="caller"):
        mdb.get_agent_output()
    await mdb.aclose()


# blocks 진입점은 caller 없이도 통과 (가드 없음)

def test_sync_blocks_entry_not_guarded(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    mdb = MongoDBManager()
    col = mdb.get_collection("kw_gp")
    assert col is not None
    MongoDBManager.reset()


async def test_async_blocks_entry_not_guarded(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.aio import AsyncMongoDBManager

    mdb = AsyncMongoDBManager()
    col = mdb.get_collection("kw_gp")
    assert col is not None
    await mdb.aclose()


# with_caller bound view는 env 없이도 envelope 통과

def test_sync_with_caller_passes_envelope_guard(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    mdb = MongoDBManager()
    col = mdb.with_caller("llm-manager").get_agent_data()
    assert col is not None
    MongoDBManager.reset()


async def test_async_with_caller_passes_envelope_guard(monkeypatch, reload_clients):
    monkeypatch.delenv("SERVICE_NAME", raising=False)
    from mdb_manager.aio import AsyncMongoDBManager

    mdb = AsyncMongoDBManager()
    col = mdb.with_caller("llm-manager").get_agent_data()
    assert col is not None
    await mdb.aclose()


# SERVICE_NAME 설정 시 envelope 진입점 통과

def test_sync_get_agent_data_passes_with_service_name(monkeypatch, reload_clients):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager.handler import MongoDBManager

    MongoDBManager.reset()
    mdb = MongoDBManager()
    col = mdb.get_agent_data()
    assert col is not None
    MongoDBManager.reset()


async def test_async_get_agent_data_passes_with_service_name(monkeypatch, reload_clients):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager.aio import AsyncMongoDBManager

    mdb = AsyncMongoDBManager()
    col = mdb.get_agent_data()
    assert col is not None
    await mdb.aclose()
