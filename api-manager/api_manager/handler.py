# -*- coding: utf-8 -*-
"""
APIManager - HTTP 클라이언트 래퍼 (Singleton)

기존 APIHandler와 유사한 인터페이스를 제공합니다.
내부적으로 API Handler Service에 HTTP 요청을 보냅니다.

사용 예시:
    from api_manager import APIManager

    api = APIManager()

    result = api.execute("finnhub_price", params={"symbol": "AAPL"})
    result = await api.aexecute("finnhub_price", params={"symbol": "AAPL"})

    api.close()
"""

import logging
from typing import Any, Optional

from api_manager._client import AsyncClient, SyncClient

logger = logging.getLogger(__name__)


class APIManager:
    """API Handler Service HTTP 클라이언트 래퍼 (Singleton)."""

    _instance: Optional["APIManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, timeout: float = 30.0) -> None:
        if self._initialized:
            return
        self._sync = SyncClient(timeout=timeout)
        self._async = AsyncClient(timeout=timeout)
        self._initialized = True
        logger.info("APIManager 초기화 완료")

    # =================================================================
    # 도구 실행
    # =================================================================

    def execute(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        format: bool = False,
    ) -> dict[str, Any]:
        """도구 동기 실행."""
        return self._sync.post(
            "/v1/execute",
            json={"tool_name": tool_name, "params": params or {}, "format": format},
        )

    async def aexecute(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
        format: bool = False,
    ) -> dict[str, Any]:
        """도구 비동기 실행."""
        return await self._async.post(
            "/v1/execute",
            json={"tool_name": tool_name, "params": params or {}, "format": format},
        )

    # =================================================================
    # 조회
    # =================================================================

    def schemas(self, only_available: bool = True) -> list[dict[str, Any]]:
        """도구 스키마 목록 반환."""
        return self._sync.get(
            "/v1/schemas", params={"only_available": only_available}
        )

    def status(self) -> dict[str, Any]:
        """API 가용 상태 확인."""
        return self._sync.get("/v1/status")

    def rate_limits(self) -> list[dict[str, Any]]:
        """Rate limit 잔여량 확인."""
        return self._sync.get("/v1/rate-limits")

    def health(self) -> dict[str, str]:
        """서비스 헬스체크."""
        return self._sync.get("/health")

    # =================================================================
    # 생명주기
    # =================================================================

    def close(self) -> None:
        if self._sync is not None:
            self._sync.close()
        logger.info("APIManager 연결 종료")

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.close()
        logger.info("APIManager 비동기 연결 종료")

    @classmethod
    def reset(cls) -> None:
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
            logger.info("APIManager Singleton 리셋 완료")

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return f"<APIManager status={status}>"
