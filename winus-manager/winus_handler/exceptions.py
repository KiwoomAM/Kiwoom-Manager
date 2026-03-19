"""SDK 예외 클래스.

기존 winus_handler 예외 인터페이스를 유지하면서,
HTTP 통신 전용 예외도 추가한다.
"""


class DatabaseException(Exception):
    """데이터베이스 관련 기본 예외 (기존 인터페이스 유지)."""

    def __init__(
        self,
        message: str,
        detail: str | None = None,
        sql: str | None = None,
        params: dict | None = None,
    ) -> None:
        self.message = message
        self.detail = detail
        self.sql = sql
        self.params = params
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]
        if self.detail:
            parts.append(f"상세: {self.detail}")
        return " | ".join(parts)


class ConnectionPoolException(DatabaseException):
    """Connection Pool 관련 예외."""
    pass


class QueryExecutionException(DatabaseException):
    """쿼리 실행 관련 예외."""
    pass


# ── SDK HTTP 전용 예외 ────────────────────────────────────


class SDKError(Exception):
    """SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class HandlerConnectionError(SDKError):
    """Handler 서비스 연결 실패."""

    def __init__(self, message: str = "WINUS Handler 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class HandlerError(SDKError):
    """Handler가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(message, status_code)
