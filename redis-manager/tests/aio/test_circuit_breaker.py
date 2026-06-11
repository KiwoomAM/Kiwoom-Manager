# -*- coding: utf-8 -*-
"""서킷 브레이커 async 통합 테스트 — sync 미러."""

import httpx
import pytest

from redis_manager._circuit import CircuitBreaker
from redis_manager.exceptions import RedisUnavailableError


def _pin_breaker(mgr, threshold=3):
    mgr._backend._breaker = CircuitBreaker(failure_threshold=threshold, reset_timeout=60.0)
    return mgr


async def test_command_path_skips_http_when_open(set_backend):
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    mgr = _pin_breaker(await set_backend(down))
    for _ in range(3):
        assert await mgr.get("k") is None
    assert calls["n"] == 3
    assert await mgr.get("k") is None      # open — HTTP 생략
    assert calls["n"] == 3


async def test_setnx_raises_immediately_when_open(set_backend):
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    mgr = _pin_breaker(await set_backend(down))
    for _ in range(3):
        await mgr.get("k")
    with pytest.raises(RedisUnavailableError):
        await mgr.setnx("lock", "owner", ttl=30)
    assert calls["n"] == 3


async def test_health_bypasses_open_circuit(set_backend):
    state = {"n": 0}

    def flaky(request):
        state["n"] += 1
        if state["n"] <= 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"status": "connected", "url": "redis://x"})

    mgr = _pin_breaker(await set_backend(flaky))
    for _ in range(3):
        await mgr.get("k")
    assert state["n"] == 3
    assert await mgr.ping() is True        # 진단 live — 브레이커 우회
    assert state["n"] == 4
    await mgr.get("k")                     # 명령 경로는 여전히 open
    assert state["n"] == 4
