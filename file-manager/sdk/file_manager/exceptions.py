# -*- coding: utf-8 -*-
"""File Manager SDK 예외 클래스."""


class FMError(Exception):
    """File Manager SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class FMConnectionError(FMError):
    """FM Service 연결 실패."""

    def __init__(
        self, message: str = "File Manager Service에 연결할 수 없습니다"
    ) -> None:
        super().__init__(message, status_code=0)


class FMServiceError(FMError):
    """FM Service가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message, status_code)
