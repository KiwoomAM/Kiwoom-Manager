# -*- coding: utf-8 -*-
"""SDK 예외 클래스."""


class MDBError(Exception):
    """MDB Handler SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MDBConnectionError(MDBError):
    """MDB Handler Service 연결 실패."""

    def __init__(
        self, message: str = "MDB Handler Service에 연결할 수 없습니다"
    ) -> None:
        super().__init__(message, status_code=0)


class MDBHandlerError(MDBError):
    """MDB Handler Service가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message, status_code)


class MDBConfigError(MDBError):
    """SDK 설정 누락 (HTTP 요청 이전에 발생). 예: SERVICE_NAME 미설정."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=0)
