# -*- coding: utf-8 -*-
"""공통 pytest fixture."""

import pytest


@pytest.fixture(autouse=True)
def reset_redis_state():
    """각 테스트 시작 전 redis-handler 글로벌 상태 초기화."""
    from redis_handler import connection
    connection._redis_client = None
    connection._redis_available = False
    yield
    connection._redis_client = None
    connection._redis_available = False
