# -*- coding: utf-8 -*-
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import redis_manager.checkpoint as ck


@pytest.fixture
def patched(monkeypatch):
    """AsyncRedis.from_url 과 AsyncRedisSaver 를 모두 목으로 대체."""
    client = MagicMock(name="client")
    client.aclose = AsyncMock()
    from_url = MagicMock(return_value=client)
    monkeypatch.setattr(ck.AsyncRedis, "from_url", from_url)

    saver = MagicMock(name="saver")
    saver.asetup = AsyncMock()
    saver_ctor = MagicMock(return_value=saver)
    monkeypatch.setattr(ck, "AsyncRedisSaver", saver_ctor)

    # 모듈 존재하는 것으로 통과시키기
    monkeypatch.setattr(ck, "_require_modules", AsyncMock())
    return types.SimpleNamespace(client=client, from_url=from_url, saver=saver, saver_ctor=saver_ctor)


@pytest.mark.asyncio
async def test_happy_path_owns_lifecycle(patched):
    async with ck.open_checkpointer() as saver:
        assert saver is patched.saver
    # 커넥션은 우리가 만들고
    patched.from_url.assert_called_once()
    # 모듈 확인 → 세이버 주입 생성 → asetup 순서
    ck._require_modules.assert_awaited_once_with(patched.client)
    patched.saver_ctor.assert_called_once_with(redis_client=patched.client)
    patched.saver.asetup.assert_awaited_once()
    # finally 에서 우리가 닫는다
    patched.client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_closes_client_even_on_error(patched):
    patched.saver.asetup.side_effect = RuntimeError("asetup boom")
    with pytest.raises(RuntimeError, match="asetup boom"):
        async with ck.open_checkpointer():
            pass
    patched.client.aclose.assert_awaited_once()  # 실패해도 커넥션 정리


@pytest.mark.asyncio
async def test_module_failure_propagates_and_closes(patched):
    ck._require_modules.side_effect = RuntimeError("no ReJSON")
    with pytest.raises(RuntimeError, match="no ReJSON"):
        async with ck.open_checkpointer():
            pass
    patched.client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_exception_propagates_unaltered(patched):
    # §6/§10: 연결 실패 시 redis-py 예외가 변형 없이(같은 인스턴스로) 전파되어야 한다.
    import redis.exceptions as rexc
    boom = rexc.ConnectionError("cannot connect")
    ck._require_modules.side_effect = boom
    with pytest.raises(rexc.ConnectionError) as exc:
        async with ck.open_checkpointer():
            pass
    assert exc.value is boom                 # 감싸거나 바꾸지 않고 그대로 전파
    patched.client.aclose.assert_awaited_once()  # 실패해도 커넥션 정리
