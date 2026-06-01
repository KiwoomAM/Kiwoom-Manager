# -*- coding: utf-8 -*-
"""RedisBackend 단위 테스트 (mock client)."""

from unittest.mock import MagicMock, patch

import pytest
import redis

from redis_handler.backends.redis_backend import RedisBackend
from redis_handler.config import RetryConfig
from redis_handler.exceptions import RedisRetryExhausted
from redis_handler.retry import RetryPolicy


class _FastConfig:
    MAX_ATTEMPTS = 2
    INITIAL_BACKOFF = 0.001
    MAX_BACKOFF = 0.001
    MULTIPLIER = 1.0
    JITTER_RATIO = 0.0


@pytest.fixture
def fast_policy():
    return RetryPolicy(_FastConfig)


@pytest.fixture
def mock_client():
    return MagicMock(spec=redis.Redis)


@pytest.fixture
def backend(mock_client, fast_policy):
    return RedisBackend(mock_client, fast_policy)


class TestRedisBackendIdentity:
    def test_name_is_redis(self, backend):
        assert backend.name == "redis"


class TestRedisBackendBasic:
    def test_get_delegates_to_client(self, backend, mock_client):
        mock_client.get.return_value = "v"
        assert backend.get("k") == "v"
        mock_client.get.assert_called_once_with("k")

    def test_delete_delegates(self, backend, mock_client):
        mock_client.delete.return_value = 1
        assert backend.delete("k") == 1

    def test_exists_returns_bool(self, backend, mock_client):
        mock_client.exists.return_value = 1
        assert backend.exists("k") is True
        mock_client.exists.return_value = 0
        assert backend.exists("k") is False

    def test_ttl_passthrough(self, backend, mock_client):
        mock_client.ttl.return_value = 42
        assert backend.ttl("k") == 42

    def test_expire_returns_bool(self, backend, mock_client):
        mock_client.expire.return_value = 1
        assert backend.expire("k", 60) is True

    def test_ping_returns_bool(self, backend, mock_client):
        mock_client.ping.return_value = True
        assert backend.ping() is True


class TestRedisBackendSet:
    def test_set_basic(self, backend, mock_client):
        mock_client.set.return_value = True
        assert backend.set("k", "v") is True
        mock_client.set.assert_called_once_with(
            "k", "v", ex=None, nx=False, xx=False, keepttl=False
        )

    def test_set_with_ex(self, backend, mock_client):
        mock_client.set.return_value = True
        backend.set("k", "v", ex=60)
        mock_client.set.assert_called_with(
            "k", "v", ex=60, nx=False, xx=False, keepttl=False
        )

    def test_set_with_nx(self, backend, mock_client):
        mock_client.set.return_value = True
        assert backend.set("k", "v", nx=True, ex=30) is True

    def test_set_nx_returns_false_when_redis_returns_none(self, backend, mock_client):
        mock_client.set.return_value = None  # NX 실패 시 redis-py None 반환
        assert backend.set("k", "v", nx=True) is False

    def test_set_zero_ex_treated_as_none(self, backend, mock_client):
        """ex=0은 redis-py에 None으로 전달 (TTL 없음)."""
        mock_client.set.return_value = True
        backend.set("k", "v", ex=0)
        mock_client.set.assert_called_with(
            "k", "v", ex=None, nx=False, xx=False, keepttl=False
        )

    def test_set_negative_ex_treated_as_none(self, backend, mock_client):
        mock_client.set.return_value = True
        backend.set("k", "v", ex=-1)
        mock_client.set.assert_called_with(
            "k", "v", ex=None, nx=False, xx=False, keepttl=False
        )

    def test_set_with_keepttl(self, backend, mock_client):
        mock_client.set.return_value = True
        backend.set("k", "v", keepttl=True)
        mock_client.set.assert_called_with(
            "k", "v", ex=None, nx=False, xx=False, keepttl=True
        )


class TestRedisBackendSetCommands:
    def test_sadd_returns_count(self, backend, mock_client):
        mock_client.sadd.return_value = 2
        assert backend.sadd("k", "a", "b") == 2
        mock_client.sadd.assert_called_with("k", "a", "b")

    def test_smembers_returns_set(self, backend, mock_client):
        mock_client.smembers.return_value = {"a", "b"}
        assert backend.smembers("k") == {"a", "b"}

    def test_srem_returns_count(self, backend, mock_client):
        mock_client.srem.return_value = 1
        assert backend.srem("k", "a") == 1


class TestRedisBackendScanIter:
    def test_scan_iter_delegates_to_client(self, backend, mock_client):
        mock_client.scan_iter.return_value = iter(["a", "b", "c"])
        result = list(backend.scan_iter("prefix:*", count=50))
        assert result == ["a", "b", "c"]
        mock_client.scan_iter.assert_called_once_with(match="prefix:*", count=50)

    def test_scan_iter_default_count(self, backend, mock_client):
        mock_client.scan_iter.return_value = iter([])
        list(backend.scan_iter("p:*"))
        mock_client.scan_iter.assert_called_once_with(match="p:*", count=100)

    def test_scan_iter_wraps_connection_error_as_RedisRetryExhausted(self, backend, mock_client):
        """generator라 @retryable 미적용. 대신 RETRYABLE_EXCEPTIONS는 도메인
        예외로 wrap되어 raise — Handler 폴백 분기 일관성 보장."""
        mock_client.scan_iter.side_effect = redis.ConnectionError("down")
        with pytest.raises(RedisRetryExhausted):
            list(backend.scan_iter("p:*"))
        assert mock_client.scan_iter.call_count == 1

    def test_scan_iter_does_not_wrap_other_errors(self, backend, mock_client):
        """ResponseError 등 비-RETRYABLE 예외는 그대로 전파."""
        mock_client.scan_iter.side_effect = redis.ResponseError("BAD")
        with pytest.raises(redis.ResponseError):
            list(backend.scan_iter("p:*"))


class TestRedisBackendRetry:
    def test_get_retries_on_connection_error(self, backend, mock_client):
        mock_client.get.side_effect = [redis.ConnectionError("d"), "v"]
        with patch("time.sleep"):
            assert backend.get("k") == "v"
        assert mock_client.get.call_count == 2

    def test_set_retries_on_timeout(self, backend, mock_client):
        mock_client.set.side_effect = [redis.TimeoutError("t"), True]
        with patch("time.sleep"):
            assert backend.set("k", "v") is True

    def test_get_raises_RedisRetryExhausted_on_persistent_failure(self, backend, mock_client):
        mock_client.get.side_effect = redis.ConnectionError("forever")
        with patch("time.sleep"):
            with pytest.raises(RedisRetryExhausted):
                backend.get("k")

    def test_response_error_propagates_immediately(self, backend, mock_client):
        """ResponseError(WRONGTYPE 등)는 재시도 없이 즉시 전파."""
        mock_client.get.side_effect = redis.ResponseError("WRONGTYPE")
        with pytest.raises(redis.ResponseError):
            backend.get("k")
        assert mock_client.get.call_count == 1
