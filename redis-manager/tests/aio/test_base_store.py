# -*- coding: utf-8 -*-
"""AsyncBaseJsonStore 단위 테스트."""

import httpx
import pytest

from redis_manager.aio.backend import AsyncHandlerBackend
from redis_manager.aio.base_store import AsyncBaseJsonStore
from redis_manager.exceptions import RedisUnavailableError


def _manager_with(handler_fn):
    """MockTransport backend를 주입한 AsyncRedisManager (self-contained)."""
    from redis_manager.aio.handler import AsyncRedisManager

    mgr = AsyncRedisManager()
    transport = httpx.MockTransport(handler_fn)
    mgr._backend = AsyncHandlerBackend(
        client=httpx.AsyncClient(transport=transport, base_url="http://test")
    )
    return mgr


class _DummyStore(AsyncBaseJsonStore):
    def __init__(self, handler):
        super().__init__(prefix="dummy", default_ttl=60, handler=handler)


async def test_make_key_prefixes():
    store = _DummyStore(handler=_manager_with(lambda req: httpx.Response(200, json={})))
    assert store._make_key("a", "b") == "dummy:a:b"


async def test_set_json_and_get_json_roundtrip():
    backing = {}

    def handler(req):
        if req.method == "POST":
            backing["v"] = req.read().decode()
            return httpx.Response(200, json={"ok": True})
        # GET — 저장된 JSON 문자열에서 value 추출
        import json as _json
        stored = _json.loads(backing["v"])["value"]
        return httpx.Response(200, json={"value": stored})

    store = _DummyStore(handler=_manager_with(handler))
    assert await store._set_json(store._make_key("x"), {"n": 1}) is True
    assert await store._get_json(store._make_key("x")) == {"n": 1}


async def test_setnx_json_blocks_fallback_when_handler_down():
    def handler(req):
        raise httpx.ConnectError("down")

    store = _DummyStore(handler=_manager_with(handler))
    with pytest.raises(RedisUnavailableError):
        await store._setnx_json(store._make_key("lock"), {"owner": "A"})


async def test_scan_iter_yields_matching_keys():
    pages = [{"cursor": 0, "keys": ["dummy:1", "dummy:2"]}]

    def handler(req):
        return httpx.Response(200, json=pages.pop(0))

    store = _DummyStore(handler=_manager_with(handler))
    out = [k async for k in store._scan_iter(store._make_key("*"))]
    assert out == ["dummy:1", "dummy:2"]
