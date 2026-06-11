# -*- coding: utf-8 -*-
"""공통 pytest fixture (v3).

기본 상태: handler 미도달 (ConnectError MockTransport) — v2의
`_redis_available = False` 직접 주입과 동일한 의미. 연결된 handler가
필요한 테스트는 set_backend fixture 사용.
"""

import httpx
import pytest

from redis_manager.backends.handler_backend import HandlerBackend
from redis_manager.handler import RedisManager


def make_mock_backend(handler_fn) -> HandlerBackend:
    """MockTransport 기반 HandlerBackend 생성 헬퍼."""
    transport = httpx.MockTransport(handler_fn)
    return HandlerBackend(client=httpx.Client(transport=transport, base_url="http://test"))


def _handler_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("handler down (test default)")


@pytest.fixture(autouse=True)
def offline_manager():
    """각 테스트 전 Singleton 리셋 + 기본 'handler 미도달' backend 주입.

    실제 네트워크로 나가는 호출을 차단한다.
    """
    RedisManager._instance = None
    manager = RedisManager()
    manager._backend.close()
    manager._backend = make_mock_backend(_handler_down)
    yield manager
    manager._backend.close()
    RedisManager._instance = None


@pytest.fixture
def set_backend(offline_manager):
    """테스트별 MockTransport handler_fn으로 backend를 교체하는 헬퍼."""

    def _set(handler_fn) -> RedisManager:
        offline_manager._backend.close()
        offline_manager._backend = make_mock_backend(handler_fn)
        return offline_manager

    return _set
