# -*- coding: utf-8 -*-
"""
API Handler SDK - HTTP 클라이언트

사용법:
    from api_handler_sdk import APIHandlerSDK

    api = APIHandlerSDK()

    # 도구 실행
    result = api.execute("finnhub_price", params={"symbol": "AAPL"})
    result = await api.aexecute("finnhub_price", params={"symbol": "AAPL"})

    # 스키마 조회
    schemas = api.schemas()

    # 상태 확인
    status = api.status()

    # Rate limit 확인
    limits = api.rate_limits()
"""

from .handler import APIHandlerSDK
from .exceptions import APIHandlerSDKError, APIHandlerConnectionError

__all__ = [
    "APIHandlerSDK",
    "APIHandlerSDKError",
    "APIHandlerConnectionError",
]
