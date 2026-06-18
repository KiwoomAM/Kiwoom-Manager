# -*- coding: utf-8 -*-
"""RedisManager orchestrator 테스트."""

import json

import httpx
import pytest

from redis_manager.backends.handler_backend import HandlerBackend
from redis_manager.exceptions import (
    RedisHandlerError,
    RedisRetryExhausted,
    RedisUnavailableError,
)
from redis_manager.handler import RedisManager


def make_mock_backend(handler_fn) -> HandlerBackend:
    """MockTransport 기반 HandlerBackend 생성 헬퍼 (conftest 동일 구현)."""
    transport = httpx.MockTransport(handler_fn)
    return HandlerBackend(client=httpx.Client(transport=transport, base_url="http://test"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _ok_get(value):
    """GET /v1/keys/<key> 응답 핸들러."""
    def handler(request):
        return httpx.Response(200, json={"value": value})
    return handler


def _ok_set(ok=True):
    """POST /v1/keys/<key>/set 응답 핸들러."""
    def handler(request):
        return httpx.Response(200, json={"ok": ok})
    return handler


def _connect_error(request):
    raise httpx.ConnectError("down")


def _retry_exhausted(request):
    return httpx.Response(503, json={"error": "Redis SET 실패 (3회 재시도 소진)"})


def _scan_response(keys):
    def handler(request):
        return httpx.Response(200, json={"cursor": 0, "keys": keys})
    return handler


def _sadd_response(count):
    def handler(request):
        return httpx.Response(200, json={"count": count})
    return handler


def _health_connected(request):
    return httpx.Response(200, json={"status": "connected", "url": "x", "redis_version": "7"})


# ── tests ────────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_returns_same_instance(self):
        a = RedisManager()
        b = RedisManager()
        assert a is b


class TestStorageMode:
    def test_memory_mode_when_redis_unavailable(self, offline_manager):
        # offline_manager는 기본으로 ConnectError backend — storage_mode == "memory"
        assert offline_manager.storage_mode == "memory"

    def test_redis_mode_when_available(self, set_backend):
        handler = set_backend(_health_connected)
        assert handler.storage_mode == "redis"


class TestNormalCommandFallback:
    def test_get_falls_back_to_memory_on_redis_failure(self, offline_manager):
        # backend는 기본 ConnectError → get 폴백 → 메모리에서 조회
        offline_manager._memory.set("k", "memory-value")
        assert offline_manager.get("k") == "memory-value"

    def test_set_falls_back_to_memory(self, offline_manager):
        assert offline_manager.set("k", "v") is True
        assert offline_manager._memory.get("k") == "v"

    def test_get_uses_redis_when_available(self, set_backend):
        handler = set_backend(_ok_get("redis-value"))
        assert handler.get("k") == "redis-value"


class TestNXFallbackBlocking:
    def test_setnx_raises_when_redis_unavailable(self, offline_manager):
        with pytest.raises(RedisUnavailableError):
            offline_manager.setnx("lock", "owner")

    def test_set_with_nx_raises_when_redis_unavailable(self, offline_manager):
        with pytest.raises(RedisUnavailableError):
            offline_manager.set("lock", "owner", nx=True)

    def test_set_with_xx_raises_when_redis_unavailable(self, offline_manager):
        with pytest.raises(RedisUnavailableError):
            offline_manager.set("k", "v", xx=True)

    def test_setnx_propagates_RedisRetryExhausted_no_fallback(self, set_backend):
        """NX는 retry 소진 시에도 메모리 폴백 안 함."""
        handler = set_backend(_retry_exhausted)
        with pytest.raises(RedisRetryExhausted):
            handler.setnx("lock", "owner", ttl=30)

    def test_setnx_returns_true_when_redis_succeeds(self, set_backend):
        handler = set_backend(_ok_set(True))
        assert handler.setnx("lock", "owner", ttl=30) is True

    def test_setnx_returns_false_when_already_held(self, set_backend):
        handler = set_backend(_ok_set(False))
        assert handler.setnx("lock", "owner", ttl=30) is False


class TestCompareAndSwapFallbackBlocking:
    """CAS는 분산 락 정합성 명령 — NX/XX처럼 메모리 폴백을 차단한다."""

    def test_compare_and_delete_raises_when_redis_unavailable(self, offline_manager):
        with pytest.raises(RedisUnavailableError):
            offline_manager.compare_and_delete("lock", "token-A")

    def test_compare_and_set_raises_when_redis_unavailable(self, offline_manager):
        with pytest.raises(RedisUnavailableError):
            offline_manager.compare_and_set("lock", "token-A", "token-A", ex=30)

    def test_compare_and_delete_propagates_retry_exhausted_no_fallback(self, set_backend):
        handler = set_backend(_retry_exhausted)
        with pytest.raises(RedisRetryExhausted):
            handler.compare_and_delete("lock", "token-A")

    def test_compare_and_delete_returns_true_when_owner_matches(self, set_backend):
        handler = set_backend(_ok_set(True))
        assert handler.compare_and_delete("lock", "token-A") is True

    def test_compare_and_delete_returns_false_when_owner_mismatches(self, set_backend):
        handler = set_backend(_ok_set(False))
        assert handler.compare_and_delete("lock", "token-A") is False

    def test_compare_and_set_returns_true_when_owner_matches(self, set_backend):
        handler = set_backend(_ok_set(True))
        assert handler.compare_and_set("lock", "token-A", "token-A", ex=60) is True

    def test_compare_and_set_sends_expected_value_ex(self, set_backend):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            captured["path"] = request.url.path
            return httpx.Response(200, json={"ok": True})

        rm = set_backend(handler)
        rm.compare_and_set("lock:r", "token-A", "token-B", ex=45)
        assert captured["body"] == {"expected": "token-A", "value": "token-B", "ex": 45}
        assert captured["path"].endswith("/compare-and-set")


class TestSetSet:
    def test_sadd_falls_back_to_memory(self, offline_manager):
        offline_manager.sadd("s", "a", "b")
        assert offline_manager._memory.smembers("s") == {"a", "b"}


class TestScanIter:
    def test_scan_iter_uses_redis_when_available(self, set_backend):
        captured = {}

        def handler(request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"cursor": 0, "keys": ["k1", "k2"]})

        rm = set_backend(handler)
        result = list(rm.scan_iter("p:*"))
        assert result == ["k1", "k2"]
        assert captured["params"]["match"] == "p:*"
        assert captured["params"]["count"] == "100"

    def test_scan_iter_falls_back_to_memory_on_redis_failure(self, offline_manager):
        """Redis SCAN이 ConnectError로 실패하면 메모리 백엔드에서 SCAN."""
        offline_manager._memory.set("mem:a", "v")
        offline_manager._memory.set("mem:b", "v")
        result = sorted(offline_manager.scan_iter("mem:*"))
        assert result == ["mem:a", "mem:b"]

    def test_scan_iter_uses_memory_when_redis_unavailable(self, offline_manager):
        offline_manager._memory.set("only:a", "v")
        result = list(offline_manager.scan_iter("only:*"))
        assert result == ["only:a"]

    def test_scan_iter_passes_count_through(self, set_backend):
        captured = {}

        def handler(request):
            captured["count"] = dict(request.url.params).get("count")
            return httpx.Response(200, json={"cursor": 0, "keys": []})

        rm = set_backend(handler)
        list(rm.scan_iter("p:*", count=500))
        assert captured["count"] == "500"


class TestHealthCheck:
    def test_health_check_includes_storage_mode(self, offline_manager):
        result = offline_manager.health_check()
        assert "connected" in result
        assert "storage" in result
        assert result["storage"] in ("redis", "memory")
