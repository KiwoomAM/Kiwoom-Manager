"""WINUS Oracle 핸들러 호환 예외 인터페이스와 HTTP SDK 전용 예외를 정의한다."""

import httpx


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


class ConsumerTimeoutError(SDKError, httpx.TimeoutException):
    """소비자 타임아웃이 지나 응답을 기다리기를 그만둠.

    요청은 핸들러에 닿았으므로 조회가 원장에서 서버 마감까지 계속 돌 수 있다. 급히 끊어야
    하면 운영자가 `client_query_id` 로 끊는다. DML 이면 커밋까지 갔는지도 알 수 없으므로
    다시 쓰기 전에 반영됐는지 확인해야 한다. 핸들러에 닿지도 못한 타임아웃은 조회가 돌지
    않았으므로 `HandlerConnectionError` 로 올린다.

    `httpx.TimeoutException` 도 상속하는 것은 임시 조치다. 이 예외가 생기기 전 소비자가
    날것의 `httpx` 타임아웃을 잡아 재시도해 왔으므로 그 `except` 를 깨지 않는다. 소비자가
    모두 이 예외로 옮겨 가면 다음 major 버전에서 뗀다.
    """

    def __init__(
        self,
        message: str,
        client_query_id: str | None = None,
        request: httpx.Request | None = None,
    ) -> None:
        self.client_query_id = client_query_id
        super().__init__(message, status_code=0)
        if request is not None:
            # 옛 `except httpx.TimeoutException` 블록이 읽던 속성이다. 비워 두면 읽는 순간 깨진다.
            self.request = request


class HandlerError(SDKError):
    """Handler가 에러 응답을 반환.

    `request_id` 는 핸들러가 요청마다 발급한 번호다. 핸들러 로그와 관리 목록에서
    그 요청을 찾을 때 쓴다.
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.detail = detail
        self.request_id = request_id
        super().__init__(message, status_code)


class QueryCancelledError(HandlerError):
    """운영자나 마감, 또는 원본(DBA 세션 종료 등)이 원장에서 끊은 조회 (`409`).

    그대로 재시도하지 않는다. 쿼리가 틀린 것도 서버가 잠시 바쁜 것도 아니므로,
    재시도하면 끊은 쪽이 멈춘 조회가 되살아난다. 사람이 읽을 사유는 `detail` 에
    있으나, 누가 끊었는지 밝히지 않는 경우도 있어 분기에는 쓰지 않는다.
    """
