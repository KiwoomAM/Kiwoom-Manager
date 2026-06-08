# -*- coding: utf-8 -*-
"""API Manager 예외 클래스."""


class APIManagerError(Exception):
    """API Manager 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class APIManagerConnectionError(APIManagerError):
    """API Handler Service 연결 실패."""

    def __init__(
        self, message: str = "API Handler Service에 연결할 수 없습니다"
    ) -> None:
        super().__init__(message, status_code=0)
