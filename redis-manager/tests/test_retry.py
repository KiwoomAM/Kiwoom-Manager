# -*- coding: utf-8 -*-
"""RetryPolicy 단위 테스트."""

import socket
from unittest.mock import MagicMock, patch

import pytest
import redis

from redis_handler.config import RetryConfig
from redis_handler.exceptions import RedisRetryExhausted
from redis_handler.retry import RetryPolicy, RETRYABLE_EXCEPTIONS


class _CustomConfig:
    """테스트용 커스텀 RetryConfig."""
    MAX_ATTEMPTS = 3
    INITIAL_BACKOFF = 0.001  # 1ms — 빠른 테스트
    MAX_BACKOFF = 0.01
    MULTIPLIER = 2.0
    JITTER_RATIO = 0.0  # jitter 비활성


class TestRetryPolicy:
    def test_succeeds_on_first_attempt(self):
        """첫 시도 성공 시 재시도 없음."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(return_value="ok")
        result = policy.execute(fn, op_name="test")
        assert result == "ok"
        assert fn.call_count == 1

    def test_succeeds_after_transient_failures(self):
        """일시 장애 후 회복."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=[
            redis.ConnectionError("disconnected"),
            redis.TimeoutError("timeout"),
            "ok",
        ])
        with patch("time.sleep"):  # 실제 sleep skip
            result = policy.execute(fn, op_name="test")
        assert result == "ok"
        assert fn.call_count == 3

    def test_exhausts_and_raises_RedisRetryExhausted(self):
        """모든 시도 실패 시 RedisRetryExhausted."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=redis.ConnectionError("forever down"))
        with patch("time.sleep"):
            with pytest.raises(RedisRetryExhausted) as exc_info:
                policy.execute(fn, op_name="test_op")
        assert "test_op" in str(exc_info.value)
        assert fn.call_count == _CustomConfig.MAX_ATTEMPTS
        # original cause 보존
        assert isinstance(exc_info.value.__cause__, redis.ConnectionError)

    def test_non_retryable_error_raised_immediately(self):
        """ResponseError 같은 비대상 예외는 즉시 전파."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=redis.ResponseError("WRONGTYPE"))
        with pytest.raises(redis.ResponseError):
            policy.execute(fn, op_name="test")
        assert fn.call_count == 1  # 재시도 없음

    def test_value_error_raised_immediately(self):
        """프로그래밍 오류(ValueError)는 즉시 전파."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError):
            policy.execute(fn, op_name="test")
        assert fn.call_count == 1

    def test_socket_timeout_is_retryable(self):
        """socket.timeout은 재시도 대상."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=[socket.timeout("t"), "ok"])
        with patch("time.sleep"):
            result = policy.execute(fn, op_name="test")
        assert result == "ok"

    def test_oserror_is_retryable(self):
        """OSError(저수준 네트워크)는 재시도 대상."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=[OSError("nw"), "ok"])
        with patch("time.sleep"):
            result = policy.execute(fn, op_name="test")
        assert result == "ok"

    def test_backoff_grows_exponentially(self):
        """백오프가 multiplier만큼 증가."""
        policy = RetryPolicy(_CustomConfig)
        fn = MagicMock(side_effect=[
            redis.ConnectionError(""),
            redis.ConnectionError(""),
            "ok",
        ])
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            policy.execute(fn, op_name="test")
        # 1차 backoff = INITIAL_BACKOFF = 0.001
        # 2차 backoff = 0.001 * 2.0 = 0.002
        assert sleep_calls == [0.001, 0.002]

    def test_backoff_capped_at_max(self):
        """백오프가 MAX_BACKOFF를 넘지 않음."""
        class CapConfig(_CustomConfig):
            MAX_ATTEMPTS = 5
            INITIAL_BACKOFF = 0.005
            MAX_BACKOFF = 0.01
            MULTIPLIER = 10.0  # 일부러 큰 배율

        policy = RetryPolicy(CapConfig)
        fn = MagicMock(side_effect=[redis.ConnectionError("")] * 4 + ["ok"])
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            policy.execute(fn, op_name="test")
        # 0.005 → 0.05 (cap to 0.01) → 0.01 → 0.01
        assert sleep_calls[0] == 0.005
        for s in sleep_calls[1:]:
            assert s == 0.01

    def test_jitter_disabled_when_ratio_zero(self):
        """JITTER_RATIO=0이면 정확히 backoff만큼 대기."""
        policy = RetryPolicy(_CustomConfig)  # ratio=0
        fn = MagicMock(side_effect=[redis.ConnectionError(""), "ok"])
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            policy.execute(fn, op_name="test")
        assert sleep_calls == [_CustomConfig.INITIAL_BACKOFF]  # 정확히 일치

    def test_retryable_exceptions_tuple(self):
        """RETRYABLE_EXCEPTIONS에 필요한 예외 모두 포함."""
        assert redis.ConnectionError in RETRYABLE_EXCEPTIONS
        assert redis.TimeoutError in RETRYABLE_EXCEPTIONS
        assert redis.BusyLoadingError in RETRYABLE_EXCEPTIONS
        assert socket.timeout in RETRYABLE_EXCEPTIONS
        assert OSError in RETRYABLE_EXCEPTIONS


class TestRetryableDecorator:
    def test_retryable_uses_self_retry_policy(self):
        """@retryable 데코레이터가 self._retry를 사용."""
        from redis_handler.retry import retryable

        class Stub:
            def __init__(self):
                self._retry = RetryPolicy(_CustomConfig)
                self.calls = 0

            @retryable
            def op(self):
                self.calls += 1
                if self.calls < 2:
                    raise redis.ConnectionError("transient")
                return "done"

        with patch("time.sleep"):
            stub = Stub()
            assert stub.op() == "done"
            assert stub.calls == 2
