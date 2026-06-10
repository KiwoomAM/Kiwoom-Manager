# -*- coding: utf-8 -*-
"""HandlerBackend 단위 테스트 — httpx.MockTransport 기반, 네트워크 없음."""

import json

import httpx
import pytest

from redis_manager.backends.handler_backend import HandlerBackend
from redis_manager.exceptions import (
    RedisHandlerError,
    RedisRetryExhausted,
    RedisUnavailableError,
)


def make_backend(handler_fn) -> HandlerBackend:
    transport = httpx.MockTransport(handler_fn)
    return HandlerBackend(client=httpx.Client(transport=transport, base_url="http://test"))


def test_get_returns_value():
    def handler(request):
        assert request.url.path == "/v1/keys/foo"
        return httpx.Response(200, json={"value": "bar"})

    assert make_backend(handler).get("foo") == "bar"


def test_get_missing_returns_none():
    backend = make_backend(lambda r: httpx.Response(200, json={"value": None}))
    assert backend.get("nope") is None


def test_set_sends_options_and_returns_ok():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    backend = make_backend(handler)
    assert backend.set("k", "v", ex=60, nx=True) is True
    assert captured["path"] == "/v1/keys/k/set"
    assert captured["body"] == {"value": "v", "ex": 60, "nx": True, "xx": False, "keepttl": False}


def test_set_nx_condition_unmet_returns_false():
    backend = make_backend(lambda r: httpx.Response(200, json={"ok": False}))
    assert backend.set("k", "v", nx=True) is False


def test_key_with_special_chars_is_quoted():
    def handler(request):
        # httpx decodes path for .path; raw_path holds the wire-level bytes
        assert request.url.raw_path == b"/v1/keys/lock%3Ajob%3A1"
        return httpx.Response(200, json={"value": "x"})

    assert make_backend(handler).get("lock:job:1") == "x"


def test_delete_exists_ttl_expire():
    routes = {
        ("DELETE", "/v1/keys/k"): {"deleted": 1},
        ("GET", "/v1/keys/k/exists"): {"exists": True},
        ("GET", "/v1/keys/k/ttl"): {"ttl": 42},
        ("POST", "/v1/keys/k/expire"): {"ok": True},
    }

    def handler(request):
        return httpx.Response(200, json=routes[(request.method, request.url.path)])

    backend = make_backend(handler)
    assert backend.delete("k") == 1
    assert backend.exists("k") is True
    assert backend.ttl("k") == 42
    assert backend.expire("k", 30) is True


def test_scan_iter_paginates_until_cursor_zero():
    pages = {0: (3, ["a", "b"]), 3: (0, ["c"])}

    def handler(request):
        cursor = int(dict(request.url.params)["cursor"])
        next_cursor, keys = pages[cursor]
        return httpx.Response(200, json={"cursor": next_cursor, "keys": keys})

    assert list(make_backend(handler).scan_iter("*", count=2)) == ["a", "b", "c"]


def test_set_operations():
    routes = {
        ("POST", "/v1/sets/s/add"): {"count": 2},
        ("GET", "/v1/sets/s"): {"members": ["a", "b"]},
        ("POST", "/v1/sets/s/remove"): {"count": 1},
    }

    def handler(request):
        return httpx.Response(200, json=routes[(request.method, request.url.path)])

    backend = make_backend(handler)
    assert backend.sadd("s", "a", "b") == 2
    assert backend.smembers("s") == {"a", "b"}
    assert backend.srem("s", "a") == 1


def test_transport_error_raises_unavailable():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(RedisUnavailableError):
        make_backend(handler).get("k")


def test_503_raises_retry_exhausted():
    backend = make_backend(
        lambda r: httpx.Response(503, json={"error": "Redis SET 실패 (3회 재시도 소진)"})
    )
    with pytest.raises(RedisRetryExhausted):
        backend.set("k", "v")


def test_other_http_error_raises_handler_error():
    backend = make_backend(lambda r: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(RedisHandlerError):
        backend.get("k")


def test_ping_true_when_connected():
    backend = make_backend(
        lambda r: httpx.Response(200, json={"status": "connected", "url": "x", "redis_version": "7"})
    )
    assert backend.ping() is True


def test_ping_false_when_redis_error_status():
    backend = make_backend(lambda r: httpx.Response(200, json={"status": "error", "error": "down"}))
    assert backend.ping() is False


def test_ping_false_when_handler_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert make_backend(handler).ping() is False
