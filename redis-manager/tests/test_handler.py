# -*- coding: utf-8 -*-
"""RedisHandler orchestrator 테스트."""

from unittest.mock import MagicMock, patch

import pytest
import redis

from redis_manager import connection
from redis_manager.exceptions import (
    RedisHandlerError,
    RedisRetryExhausted,
    RedisUnavailableError,
)
from redis_manager.handler import RedisHandler


@pytest.fixture
def handler():
    h = RedisHandler()
    h.reset()
    return h


def _install_mock_client(mock_client):
    """connection 모듈에 mock client 주입."""
    connection._redis_client = mock_client
    connection._redis_available = True


class TestSingleton:
    def test_returns_same_instance(self):
        a = RedisHandler()
        b = RedisHandler()
        assert a is b


class TestStorageMode:
    def test_memory_mode_when_redis_unavailable(self, handler):
        connection._redis_client = None
        connection._redis_available = False
        assert handler.storage_mode == "memory"

    def test_redis_mode_when_available(self, handler):
        _install_mock_client(MagicMock(spec=redis.Redis))
        assert handler.storage_mode == "redis"


class TestNormalCommandFallback:
    def test_get_falls_back_to_memory_on_redis_failure(self, handler):
        # Redis는 항상 connection error
        client = MagicMock(spec=redis.Redis)
        client.get.side_effect = redis.ConnectionError("down")
        _install_mock_client(client)
        # 메모리에 미리 값 저장
        handler._memory.set("k", "memory-value")
        with patch("time.sleep"):
            assert handler.get("k") == "memory-value"

    def test_set_falls_back_to_memory(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.set.side_effect = redis.ConnectionError("down")
        _install_mock_client(client)
        with patch("time.sleep"):
            assert handler.set("k", "v") is True
        assert handler._memory.get("k") == "v"

    def test_get_uses_redis_when_available(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = "redis-value"
        _install_mock_client(client)
        assert handler.get("k") == "redis-value"


class TestNXFallbackBlocking:
    def test_setnx_raises_when_redis_unavailable(self, handler):
        connection._redis_client = None
        connection._redis_available = False
        with pytest.raises(RedisUnavailableError):
            handler.setnx("lock", "owner")

    def test_set_with_nx_raises_when_redis_unavailable(self, handler):
        connection._redis_client = None
        connection._redis_available = False
        with pytest.raises(RedisUnavailableError):
            handler.set("lock", "owner", nx=True)

    def test_set_with_xx_raises_when_redis_unavailable(self, handler):
        connection._redis_client = None
        connection._redis_available = False
        with pytest.raises(RedisUnavailableError):
            handler.set("k", "v", xx=True)

    def test_setnx_propagates_RedisRetryExhausted_no_fallback(self, handler):
        """NX는 retry 소진 시에도 메모리 폴백 안 함."""
        client = MagicMock(spec=redis.Redis)
        client.set.side_effect = redis.ConnectionError("down")
        _install_mock_client(client)
        with patch("time.sleep"):
            with pytest.raises(RedisRetryExhausted):
                handler.setnx("lock", "owner", ttl=30)

    def test_setnx_returns_true_when_redis_succeeds(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.set.return_value = True
        _install_mock_client(client)
        assert handler.setnx("lock", "owner", ttl=30) is True

    def test_setnx_returns_false_when_already_held(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.set.return_value = None  # NX 실패
        _install_mock_client(client)
        assert handler.setnx("lock", "owner", ttl=30) is False


class TestSetSet:
    def test_sadd_falls_back_to_memory(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.sadd.side_effect = redis.ConnectionError("d")
        _install_mock_client(client)
        with patch("time.sleep"):
            handler.sadd("s", "a", "b")
        assert handler._memory.smembers("s") == {"a", "b"}


class TestScanIter:
    def test_scan_iter_uses_redis_when_available(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.scan_iter.return_value = iter(["k1", "k2"])
        _install_mock_client(client)
        result = list(handler.scan_iter("p:*"))
        assert result == ["k1", "k2"]
        client.scan_iter.assert_called_once_with(match="p:*", count=100)

    def test_scan_iter_falls_back_to_memory_on_redis_failure(self, handler):
        """Redis SCAN이 ConnectionError로 실패하면 메모리 백엔드에서 SCAN.

        RedisBackend.scan_iter가 ConnectionError를 RedisRetryExhausted로 wrap
        하므로 Handler의 except RedisHandlerError가 catch하고 폴백한다.
        """
        client = MagicMock(spec=redis.Redis)
        client.scan_iter.side_effect = redis.ConnectionError("down")
        _install_mock_client(client)
        handler._memory.set("mem:a", "v")
        handler._memory.set("mem:b", "v")
        result = sorted(handler.scan_iter("mem:*"))
        assert result == ["mem:a", "mem:b"]

    def test_scan_iter_uses_memory_when_redis_unavailable(self, handler):
        connection._redis_client = None
        connection._redis_available = False
        handler._memory.set("only:a", "v")
        result = list(handler.scan_iter("only:*"))
        assert result == ["only:a"]

    def test_scan_iter_passes_count_through(self, handler):
        client = MagicMock(spec=redis.Redis)
        client.scan_iter.return_value = iter([])
        _install_mock_client(client)
        list(handler.scan_iter("p:*", count=500))
        client.scan_iter.assert_called_once_with(match="p:*", count=500)


class TestHealthCheck:
    def test_health_check_includes_storage_mode(self, handler):
        result = handler.health_check()
        assert "connected" in result
        assert "storage" in result
        assert result["storage"] in ("redis", "memory")
