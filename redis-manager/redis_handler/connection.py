# -*- coding: utf-8 -*-
"""
redis_handler/connection.py

Redis 연결 관리 (Singleton)

redis-py 클라이언트의 라이프사이클을 관리합니다.
Redis가 없으면 None을 반환하며, 각 Store가 메모리 폴백으로 동작합니다.
"""

import logging
from typing import Optional

from .config import RedisConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Redis 클라이언트 (Singleton)
# =============================================================================

_redis_client = None
_redis_available: bool = False


def init_redis() -> bool:
    """
    Redis 클라이언트 초기화

    RedisConfig의 설정을 사용하여 redis-py 클라이언트를 생성합니다.
    연결에 실패하면 False를 반환하며, 각 Store는 메모리 폴백으로 동작합니다.

    Returns:
        Redis 연결 성공 여부
    """
    global _redis_client, _redis_available

    if not RedisConfig.is_configured():
        logger.warning("Redis URL이 설정되지 않음 - 메모리 저장소 사용")
        _redis_available = False
        return False

    try:
        import redis

        _redis_client = redis.from_url(
            RedisConfig.REDIS_URL,
            decode_responses=True,
            socket_timeout=RedisConfig.SOCKET_TIMEOUT,
            socket_connect_timeout=RedisConfig.SOCKET_CONNECT_TIMEOUT,
        )
        # 연결 테스트
        _redis_client.ping()
        _redis_available = True
        logger.info(f"Redis 연결 성공: {RedisConfig.REDIS_URL}")
        return True

    except ImportError:
        logger.warning("redis 패키지가 설치되지 않음 - 메모리 저장소 사용")
        _redis_available = False
        return False

    except Exception as e:
        logger.warning(f"Redis 연결 실패: {e} - 메모리 저장소 사용")
        _redis_available = False
        return False


def get_client():
    """
    Redis 클라이언트 반환

    초기화되지 않았으면 자동으로 init_redis()를 호출합니다.
    연결 실패 시 None을 반환합니다.

    Returns:
        redis.Redis 인스턴스 또는 None
    """
    global _redis_client, _redis_available

    if _redis_client is None and not _redis_available:
        init_redis()

    return _redis_client if _redis_available else None


def is_available() -> bool:
    """Redis 연결 가능 여부 반환"""
    return _redis_available


def close_redis() -> None:
    """Redis 클라이언트 종료"""
    global _redis_client, _redis_available

    if _redis_client is not None:
        try:
            _redis_client.close()
            logger.info("Redis 연결 종료됨")
        except Exception as e:
            logger.warning(f"Redis 연결 종료 중 오류: {e}")
        _redis_client = None
    _redis_available = False


def reset_redis() -> None:
    """Redis 클라이언트 리셋 (테스트용)"""
    global _redis_client, _redis_available
    _redis_client = None
    _redis_available = False


def check_redis_connection(verbose: bool = True) -> bool:
    """
    Redis 연결 상태 확인 (헬스 체크)

    Args:
        verbose: 상세 출력 여부

    Returns:
        연결 성공 여부
    """
    if verbose:
        print("\n Redis 설정:")
        print(f"   URL: {RedisConfig.REDIS_URL}")
        print(f"   Socket Timeout: {RedisConfig.SOCKET_TIMEOUT}s")

    client = get_client()

    if client is None:
        if verbose:
            print("   Redis 연결 실패 - 메모리 폴백 모드로 동작")
        return False

    try:
        client.ping()
        if verbose:
            print("   Redis 연결 성공")
        logger.info("Redis 연결 테스트 성공")
        return True
    except Exception as e:
        if verbose:
            print(f"   Redis 연결 실패: {e}")
        logger.error(f"Redis 연결 테스트 실패: {e}")
        return False


__all__ = [
    "init_redis",
    "get_client",
    "is_available",
    "close_redis",
    "reset_redis",
    "check_redis_connection",
]
