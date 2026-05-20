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
        error_type: 에러 출처.
            - llm-request 자체: "validation" | "cache" | "file" |
              "unsupported_file_type" | "file_too_large" | "file_resolve" | "handler"
            - llm-handler 에서 그대로 relay 된 값: "content_blocked" | "transport"
              | "provider" | "handler_ops" | "handler_internal"
              (status_code 도 llm-handler 가 정한 값 그대로 보존)
            - None: 구버전 / error_type 미상 응답
        detail: 에러 sub-cause 식별자. llm-handler 의 handler_internal 슬러그
            ("pool_timeout" | "local_protocol_error" | <ExceptionClassName>) 가
            relay 된 값 — 운영 alert 라우팅용. 없으면 None.
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        error_type: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.error_type = error_type
        self.detail = detail

    def __str__(self) -> str:
        prefix = f"[{self.error_type}] " if self.error_type else ""
        return f"{prefix}{self.message} ({self.status_code})"
