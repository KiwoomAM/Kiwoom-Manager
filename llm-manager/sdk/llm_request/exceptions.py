"""SDK 예외 클래스."""


class SDKError(Exception):
    """llm-request SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.message} ({self.status_code})" if self.status_code else self.message


class ServiceConnectionError(SDKError):
    """llm-request Service 연결 실패."""

    def __init__(self, message: str = "llm-request 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class ServiceError(SDKError):
    """Service가 에러 응답을 반환.

    Attributes:
        error_type: 에러 출처 ("validation" | "cache" | "file" | "handler" | None).
    """

    def __init__(self, message: str, status_code: int, error_type: str | None = None) -> None:
        super().__init__(message, status_code)
        self.error_type = error_type

    def __str__(self) -> str:
        prefix = f"[{self.error_type}] " if self.error_type else ""
        return f"{prefix}{self.message} ({self.status_code})"
