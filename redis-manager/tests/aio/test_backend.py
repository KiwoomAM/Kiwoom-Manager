# -*- coding: utf-8 -*-
"""AsyncHandlerBackend 단위 테스트 (MockTransport)."""

import httpx
import pytest

from redis_manager.aio.backend import AsyncHandlerBackend
from redis_manager.exceptions import (
    RedisHandlerError,
    RedisRetryExhausted,
    RedisUnavailableError,
)


def _backend(handler_fn) -> AsyncHandlerBackend:
    transport = httpx.MockTransport(handler_fn)
    return AsyncHandlerBackend(
        client=httpx.AsyncClient(transport=transport, base_url="http://test")
    )


async def test_get_returns_value():
    def handler(req):
        assert req.url.path == "/v1/keys/foo"
        return httpx.Response(200, json={"value": "bar"})

    backend = _backend(handler)
    try:
        assert await backend.get("foo") == "bar"
    finally:
        await backend.aclose()


async def test_set_posts_body_and_returns_ok():
    seen = {}

    def handler(req):
        seen["json"] = req.read().decode()
        return httpx.Response(200, json={"ok": True})

    backend = _backend(handler)
    try:
        assert await backend.set("k", "v", ex=10, nx=True) is True
    finally:
        await backend.aclose()
    assert '"value": "v"' in seen["json"] or '"value":"v"' in seen["json"]


async def test_compare_and_delete_sends_expected_and_returns_ok():
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["json"] = req.read().decode()
        return httpx.Response(200, json={"ok": True})

    backend = _backend(handler)
    try:
        assert await backend.compare_and_delete("lock", "token-A") is True
    finally:
        await backend.aclose()
    assert seen["path"] == "/v1/keys/lock/compare-and-delete"
    assert '"expected"' in seen["json"] and "token-A" in seen["json"]


async def test_compare_and_set_sends_expected_value_ex_and_returns_ok():
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["json"] = req.read().decode()
        return httpx.Response(200, json={"ok": True})

    backend = _backend(handler)
    try:
        assert await backend.compare_and_set("lock", "token-A", "token-B", ex=60) is True
    finally:
        await backend.aclose()
    assert seen["path"] == "/v1/keys/lock/compare-and-set"
    for token in ('"expected"', '"value"', '"ex"', "token-A", "token-B", "60"):
        assert token in seen["json"]


async def test_scan_iter_paginates():
    pages = [{"cursor": 7, "keys": ["a", "b"]}, {"cursor": 0, "keys": ["c"]}]

    def handler(req):
        return httpx.Response(200, json=pages.pop(0))

    backend = _backend(handler)
    try:
        out = [k async for k in backend.scan_iter("*", count=2)]
    finally:
        await backend.aclose()
    assert out == ["a", "b", "c"]


async def test_transport_error_maps_to_unavailable():
    def handler(req):
        raise httpx.ConnectError("down")

    backend = _backend(handler)
    try:
        with pytest.raises(RedisUnavailableError):
            await backend.get("foo")
    finally:
        await backend.aclose()


async def test_503_maps_to_retry_exhausted():
    def handler(req):
        return httpx.Response(503, json={"error": "재시도 소진"})

    backend = _backend(handler)
    try:
        with pytest.raises(RedisRetryExhausted):
            await backend.get("foo")
    finally:
        await backend.aclose()


async def test_4xx_maps_to_handler_error():
    def handler(req):
        return httpx.Response(400, json={"error": "bad"})

    backend = _backend(handler)
    try:
        with pytest.raises(RedisHandlerError):
            await backend.get("foo")
    finally:
        await backend.aclose()


async def test_key_with_slash_is_encoded():
    def handler(req):
        # httpx는 .path를 디코드하므로 wire-level 인코딩은 raw_path로 검증 (sync 스위트와 동일 패턴)
        assert req.url.raw_path == b"/v1/keys/a%2Fb"
        return httpx.Response(200, json={"value": None})

    backend = _backend(handler)
    try:
        assert await backend.get("a/b") is None
    finally:
        await backend.aclose()
