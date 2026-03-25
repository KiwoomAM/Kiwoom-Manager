# -*- coding: utf-8 -*-
"""
redis_handler/config.py

Redis 연결 설정 클래스

접속 정보와 소켓 설정만 관리합니다.
비즈니스 로직 관련 설정(TTL, 크기 제한 등)은 각 도메인 코드에서 관리합니다.

사용법:
    호출하는 서비스에서 .env를 먼저 로드한 후 import하세요.

    >>> from dotenv import load_dotenv
    >>> load_dotenv()
    >>> from redis_handler.config import RedisConfig

    또는 명시적으로 설정:

    >>> RedisConfig.configure(url="redis://localhost:6379/0")
"""

import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class RedisConfig:
    """
    Redis 연결 설정

    환경변수에서 설정을 로드하며, 클래스 속성으로 접근 가능합니다.
    configure() 메서드로 명시적 설정도 지원합니다.
    """

    # ── 접속 정보 ──────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://192.7.9.45:7617/0")

    # ── 소켓 설정 ──────────────────────────────────────────────
    SOCKET_TIMEOUT: int = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
    SOCKET_CONNECT_TIMEOUT: int = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))

    # ── 내부 상태 ─────────────────────────────────────────────
    _env_path: Optional[str] = None

    @classmethod
    def is_configured(cls) -> bool:
        """Redis URL이 설정되어 있는지 확인"""
        return bool(cls.REDIS_URL)

    @classmethod
    def get_env_path(cls) -> str:
        """환경변수 경로 반환"""
        return cls._env_path or "환경변수"

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        """현재 설정 요약"""
        return {
            "url": cls.REDIS_URL,
            "socket_timeout": cls.SOCKET_TIMEOUT,
            "socket_connect_timeout": cls.SOCKET_CONNECT_TIMEOUT,
            "env_path": cls.get_env_path(),
        }

    @classmethod
    def configure(
        cls,
        url: Optional[str] = None,
        socket_timeout: Optional[int] = None,
        socket_connect_timeout: Optional[int] = None,
    ) -> None:
        """명시적 설정"""
        if url is not None:
            cls.REDIS_URL = url
        if socket_timeout is not None:
            cls.SOCKET_TIMEOUT = socket_timeout
        if socket_connect_timeout is not None:
            cls.SOCKET_CONNECT_TIMEOUT = socket_connect_timeout

        logger.info(f"RedisConfig 수동 설정 완료: {cls.REDIS_URL}")

    @classmethod
    def reload_from_env(cls) -> None:
        """환경변수에서 재로드"""
        cls.REDIS_URL = os.getenv("REDIS_URL", "redis://192.7.9.45:7617/0")
        cls.SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        cls.SOCKET_CONNECT_TIMEOUT = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))

        logger.info("RedisConfig 환경변수에서 재로드 완료")


__all__ = [
    "RedisConfig",
]
