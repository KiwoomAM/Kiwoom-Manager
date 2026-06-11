# -*- coding: utf-8 -*-
"""AsyncRedisManager 단위 테스트."""

import httpx
import pytest

from redis_manager.exceptions import RedisRetryExhausted, RedisUnavailableError


async def test_set_and_get_happy_path(set_backend):
    store = {}

    def handler(req):
        if req.method == "POST":
            store["k"] = "v"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"value": store.get("k")})

    mgr = await set_backend(handler)
    assert await mgr.set("k", "v") is True
    assert await mgr.get("k") == "v"


async def test_set_ttl_ex_conflict_raises_valueerror(offline_manager):
    with pytest.raises(ValueError):
        await offline_manager.set("k", "v", ttl=10, ex=20)


async def test_set_nx_xx_conflict_raises_valueerror(offline_manager):
    with pytest.raises(ValueError):
        await offline_manager.set("k", "v", nx=True, xx=True)


async def test_general_command_falls_back_to_memory_when_handler_down(offline_manager):
    """handler 미도달 시 일반 set/get은 메모리 폴백으로 동작."""
    assert await offline_manager.set("k", "v") is True
    assert await offline_manager.get("k") == "v"  # 메모리에서 반환


async def test_setnx_does_not_fall_back_when_handler_down(offline_manager):
    """NX는 분산 잠금 의미 보호 — 폴백 안 하고 RedisUnavailableError 전파."""
    with pytest.raises(RedisUnavailableError):
        await offline_manager.setnx("lock", "owner", ttl=30)


async def test_setnx_raises_retry_exhausted_on_503(set_backend):
    def handler(req):
        return httpx.Response(503, json={"error": "재시도 소진"})

    mgr = await set_backend(handler)
    with pytest.raises(RedisRetryExhausted):
        await mgr.setnx("lock", "owner", ttl=30)


async def test_scan_iter_yields_keys(set_backend):
    pages = [{"cursor": 3, "keys": ["scan:1"]}, {"cursor": 0, "keys": ["scan:2"]}]

    def handler(req):
        return httpx.Response(200, json=pages.pop(0))

    mgr = await set_backend(handler)
    out = [k async for k in mgr.scan_iter("scan:*")]
    assert out == ["scan:1", "scan:2"]


async def test_health_check_reports_connected(set_backend):
    def handler(req):
        return httpx.Response(200, json={"status": "connected", "url": "redis://x"})

    mgr = await set_backend(handler)
    result = await mgr.health_check()
    assert result["connected"] is True
    assert result["storage"] == "redis"


async def test_context_manager_and_ensure_open():
    from redis_manager.aio.handler import AsyncRedisManager

    async with AsyncRedisManager() as mgr:
        assert mgr._closed is False
    assert mgr._closed is True
    with pytest.raises(RuntimeError):
        await mgr.get("k")
