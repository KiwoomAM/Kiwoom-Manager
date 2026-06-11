# -*- coding: utf-8 -*-
"""
redis_manager/config.py

redis-handler 접속 설정만 관리합니다. Redis 자격증명은 handler 서버 전용입니다.
비즈니스 로직 관련 설정(TTL, 크기 제한 등)은 각 도메인 코드에서 관리합니다.

사용법:
    호출하는 서비스에서 .env를 먼저 로드한 후 import하세요.

    >>> from dotenv import load_dotenv
    >>> load_dotenv()
    >>> from redis_manager.config import RedisConfig

    또는 명시적으로 설정:

    >>> RedisConfig.configure(handler_url="http://localhost:8800")
"""

import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class RedisConfig:
    """
    redis-handler 접속 설정

    환경변수에서 설정을 로드하며, 클래스 속성으로 접근 가능합니다.
    configure() 메서드로 명시적 설정도 지원합니다.

    [v3] Redis 직접 접속정보(REDIS_URL/SOCKET_*)는 제거됨 — handler 서버 전용.
    manager 사용자는 REDIS_HANDLER_URL만 알면 된다.
    """

    # ── Handler 접속 정보 (v3) ─────────────────────────────────
    HANDLER_URL: str = os.getenv("REDIS_HANDLER_URL", "http://192.7.9.45:8800")
    HANDLER_TIMEOUT: float = float(os.getenv("REDIS_HANDLER_TIMEOUT", "5"))

    # ── 내부 상태 ─────────────────────────────────────────────
    _env_path: Optional[str] = None

    @classmethod
    def is_configured(cls) -> bool:
        """handler URL이 설정되어 있는지 확인"""
        return bool(cls.HANDLER_URL)

    @classmethod
    def get_env_path(cls) -> str:
        """환경변수 경로 반환"""
        return cls._env_path or "환경변수"

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        """현재 설정 요약 (자격증명 미포함)"""
        return {
            "handler_url": cls.HANDLER_URL,
            "handler_timeout": cls.HANDLER_TIMEOUT,
            "env_path": cls.get_env_path(),
        }

    @classmethod
    def configure(
        cls,
        handler_url: Optional[str] = None,
        handler_timeout: Optional[float] = None,
    ) -> None:
        """명시적 설정"""
        if handler_url is not None:
            cls.HANDLER_URL = handler_url
        if handler_timeout is not None:
            cls.HANDLER_TIMEOUT = handler_timeout

        logger.info(f"RedisConfig 수동 설정 완료: handler={cls.HANDLER_URL}")

    @classmethod
    def reload_from_env(cls) -> None:
        """환경변수에서 재로드"""
        cls.HANDLER_URL = os.getenv("REDIS_HANDLER_URL", "http://192.7.9.45:8800")
        cls.HANDLER_TIMEOUT = float(os.getenv("REDIS_HANDLER_TIMEOUT", "5"))

        logger.info("RedisConfig 환경변수에서 재로드 완료")


class RetryConfig:
    """[v3 deprecated] retry는 redis-handler 서버로 이관 — manager에서 미사용. import 호환용 유지."""

    MAX_ATTEMPTS: int = int(os.getenv("REDIS_RETRY_MAX_ATTEMPTS", "3"))
    INITIAL_BACKOFF: float = float(os.getenv("REDIS_RETRY_INITIAL_BACKOFF", "0.1"))
    MAX_BACKOFF: float = float(os.getenv("REDIS_RETRY_MAX_BACKOFF", "1.0"))
    MULTIPLIER: float = float(os.getenv("REDIS_RETRY_MULTIPLIER", "2.0"))
    JITTER_RATIO: float = float(os.getenv("REDIS_RETRY_JITTER_RATIO", "0.2"))

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        return {
            "max_attempts": cls.MAX_ATTEMPTS,
            "initial_backoff": cls.INITIAL_BACKOFF,
            "max_backoff": cls.MAX_BACKOFF,
            "multiplier": cls.MULTIPLIER,
            "jitter_ratio": cls.JITTER_RATIO,
        }


__all__ = [
    "RedisConfig",
    "RetryConfig",
]
