# -*- coding: utf-8 -*-
"""redis_manager/retry.py

재시도 정책 + @retryable 데코레이터.

일시적 네트워크/연결 오류는 exponential backoff + jitter로 재시도.
영속적 오류(ResponseError, ValueError 등)는 즉시 전파.
"""

import functools
import logging
import random
import socket
import time
from typing import Callable, TypeVar

import redis

from .config import RetryConfig
from .exceptions import RedisRetryExhausted

logger = logging.getLogger(__name__)
T = TypeVar("T")


# 재시도 대상 예외 (일시적 장애)
RETRYABLE_EXCEPTIONS = (
    redis.ConnectionError,
    redis.TimeoutError,
    redis.BusyLoadingError,
    socket.timeout,
    OSError,
)


class RetryPolicy:
    """
    Exponential backoff + jitter 재시도 정책.

    사용법:
        policy = RetryPolicy()  # 또는 RetryPolicy(custom_config)
        result = policy.execute(lambda: client.get("key"), op_name="GET")
    """

    def __init__(self, config=None):
        self._config = config or RetryConfig

    def execute(self, fn: Callable[[], T], *, op_name: str = "redis_op") -> T:
        """fn을 재시도 정책에 따라 실행.

        Args:
            fn: 인자 없이 호출 가능한 callable
            op_name: 로그용 작업명

        Returns:
            fn의 반환값

        Raises:
            RedisRetryExhausted: 모든 재시도 소진 후 실패
            (그 외 예외): 비대상 예외는 즉시 전파
        """
        backoff = self._config.INITIAL_BACKOFF
        last_exc: Exception | None = None

        for attempt in range(1, self._config.MAX_ATTEMPTS + 1):
            try:
                return fn()
            except RETRYABLE_EXCEPTIONS as e:
                last_exc = e
                if attempt >= self._config.MAX_ATTEMPTS:
                    break
                wait = self._jitter(backoff)
                logger.warning(
                    "Redis %s 재시도 %d/%d — %s (%.3fs 대기)",
                    op_name, attempt, self._config.MAX_ATTEMPTS, e, wait,
                )
                time.sleep(wait)
                backoff = min(backoff * self._config.MULTIPLIER, self._config.MAX_BACKOFF)

        raise RedisRetryExhausted(
            f"Redis {op_name} 실패 ({self._config.MAX_ATTEMPTS}회 재시도 소진)"
        ) from last_exc

    def _jitter(self, backoff: float) -> float:
        ratio = self._config.JITTER_RATIO
        if ratio <= 0:
            return backoff
        return max(0.0, backoff * (1 + random.uniform(-ratio, ratio)))


def retryable(method):
    """
    RedisBackend 메서드 데코레이터.

    인스턴스가 self._retry: RetryPolicy를 가지고 있어야 함.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        return self._retry.execute(
            lambda: method(self, *args, **kwargs),
            op_name=method.__name__,
        )
    return wrapper


__all__ = [
    "RETRYABLE_EXCEPTIONS",
    "RetryPolicy",
    "retryable",
]
