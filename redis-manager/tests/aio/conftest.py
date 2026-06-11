# -*- coding: utf-8 -*-
"""async 테스트 공통 fixture: MockTransport 기반 AsyncHandlerBackend 주입."""

import httpx
import pytest

from redis_manager.aio.backend import AsyncHandlerBackend
from redis_manager.aio.handler import AsyncRedisManager


def make_mock_backend(handler_fn) -> AsyncHandlerBackend:
    """MockTransport 기반 AsyncHandlerBackend 생성 헬퍼."""
    transport = httpx.MockTransport(handler_fn)
    return AsyncHandlerBackend(
        client=httpx.AsyncClient(transport=transport, base_url="http://test")
    )


def handler_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("handler down (test default)")


@pytest.fixture
async def offline_manager():
    """기본 'handler 미도달' backend가 주입된 AsyncRedisManager."""
    mgr = AsyncRedisManager()
    await mgr._backend.aclose()
    mgr._backend = make_mock_backend(handler_down)
    yield mgr
    await mgr.aclose()


@pytest.fixture
def set_backend(offline_manager):
    """테스트별 MockTransport handler_fn으로 backend를 교체하는 async 헬퍼."""

    async def _set(handler_fn) -> AsyncRedisManager:
        await offline_manager._backend.aclose()
        offline_manager._backend = make_mock_backend(handler_fn)
        return offline_manager

    return _set
