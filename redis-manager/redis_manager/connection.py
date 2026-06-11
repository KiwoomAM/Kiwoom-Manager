# -*- coding: utf-8 -*-
"""
redis_manager/connection.py

[v3] redis-py 직결 제거 — redis-handler HTTP 경유로 전환됨.
본 모듈은 v2 공개 함수의 호환 shim만 제공한다. 신규 코드는 RedisManager를 사용할 것.

v2와의 차이:
    - _redis_available 캐시 플래그 없음 — 모든 확인은 live (handler /v1/admin/health)
    - get_client()는 항상 None (redis-py 클라이언트가 존재하지 않음)
"""

import logging
import warnings

from .config import RedisConfig

logger = logging.getLogger(__name__)


def init_redis() -> bool:
    """[v2 호환] handler 도달성 1회 확인 (eager warmup 용도).

    v3에서는 init이라는 상태가 없다 — live 확인 결과만 반환한다.
    """
    from .handler import RedisManager

    ok = RedisManager().ping()
    if ok:
        logger.info("redis-handler 연결 성공: %s", RedisConfig.HANDLER_URL)
    else:
        logger.warning("redis-handler 연결 실패 - 메모리 폴백 모드: %s", RedisConfig.HANDLER_URL)
    return ok


def get_client():
    """[deprecated] redis-py 클라이언트는 v3에서 존재하지 않음 — 항상 None.

    v2에서도 미연결 시 None 계약이었으므로 호출측 None 분기는 그대로 동작한다.
    """
    warnings.warn(
        "get_client()는 v3에서 항상 None을 반환합니다. RedisManager API를 사용하세요.",
        DeprecationWarning,
        stacklevel=2,
    )
    return None


def is_available() -> bool:
    """[v2 호환] live 확인 — handler /v1/admin/health 1회 호출."""
    from .handler import RedisManager

    return RedisManager().ping()


def close_redis() -> None:
    """[v2 호환] backend HTTP 클라이언트 종료."""
    from .handler import RedisManager

    RedisManager().close()


def reset_redis() -> None:
    """[v2 호환] 핸들러 리셋 (테스트용)."""
    from .handler import RedisManager

    RedisManager().reset()


def check_redis_connection(verbose: bool = True) -> bool:
    """Redis(handler) 연결 상태 확인 (헬스 체크) — live."""
    from .handler import RedisManager

    if verbose:
        print("\n Redis Handler 설정:")
        print(f"   Handler URL: {RedisConfig.HANDLER_URL}")
        print(f"   Timeout: {RedisConfig.HANDLER_TIMEOUT}s")

    ok = RedisManager().ping()
    if verbose:
        print("   연결 성공" if ok else "   연결 실패 - 메모리 폴백 모드로 동작")
    logger.info("redis-handler 연결 테스트 %s", "성공" if ok else "실패")
    return ok


__all__ = [
    "init_redis",
    "get_client",
    "is_available",
    "close_redis",
    "reset_redis",
    "check_redis_connection",
]
