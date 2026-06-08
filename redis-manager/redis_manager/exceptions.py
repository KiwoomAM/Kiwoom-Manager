# -*- coding: utf-8 -*-
"""redis_manager/exceptions.py

redis_manager 패키지의 도메인 예외 계층.

- RedisHandlerError: 모든 패키지 예외의 베이스
- RedisRetryExhausted: 재시도 정책 소진 후 실패
- RedisUnavailableError: Redis 미연결 + 폴백 불가 작업(NX/XX) 호출
"""


class RedisHandlerError(Exception):
    """redis_manager 패키지의 모든 도메인 예외 베이스."""


class RedisRetryExhausted(RedisHandlerError):
    """재시도 정책 소진 후 실패."""


class RedisUnavailableError(RedisHandlerError):
    """Redis 미연결 상태에서 폴백 불가 작업(NX/XX) 호출."""


__all__ = [
    "RedisHandlerError",
    "RedisRetryExhausted",
    "RedisUnavailableError",
]
