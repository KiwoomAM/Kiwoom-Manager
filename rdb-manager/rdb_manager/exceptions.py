"""기존 rdb_manager 호환 예외 인터페이스와 HTTP SDK 전용 예외를 정의한다.

주의: HTTP SDK 전환 이후 클라이언트가 실제로 raise하는 것은 ``HandlerError`` /
``HandlerConnectionError`` (둘 다 ``SDKError`` 하위)뿐이다. 아래 레거시 예외
(``DatabaseException`` / ``ConnectionPoolException`` / ``QueryExecutionException``)는
import 호환을 위해 정의·export만 유지되며 현재 경로에서는 raise되지 않는다.
``except QueryExecutionException`` 으로는 쿼리 오류를 잡을 수 없다.
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

    def __init__(self, message: str = "RDB Handler 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class HandlerError(SDKError):
    """Handler가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(message, status_code)
