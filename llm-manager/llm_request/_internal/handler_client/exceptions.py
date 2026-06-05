"""SDK 예외 클래스.

Service 측 [`app.core.errors`](../../service/app/core/errors.py) 의 6 exception 과
1:1 매핑. SDK 의 `_client._request` 가 `error_type` 으로 dispatch 하여 적절한
sub-class 를 raise → caller 는 `isinstance(e, TransportError)` 등으로 분기.

4 origins (error_type grouping):
    - caller    : ValidationError, ContentBlockedError
    - transport : TransportError
    - provider  : ProviderError
    - handler   : HandlerOpsError, HandlerInternalError

wire format error_type 6개 값:
    "validation" | "content_blocked" | "transport" | "provider"
    | "handler_ops" | "handler_internal"

Backward compat:
    `except HandlerError` 는 6 sub-class 전부 catch (base 유지).
    기존 `e.error_type == "validation"` / `"provider"` 는 그대로 작동.
    기존 `e.error_type == "handler"` 는 더 이상 안 옴 →
    `isinstance(e, (HandlerOpsError, HandlerInternalError))` 또는
    `e.error_type in {"handler_ops", "handler_internal"}` 으로 변경 필요.
"""


class SDKError(Exception):
    """SDK 기본 에러.

    Attributes:
        detail: handler_internal 의 sub-cause slug (운영 alert 라우팅용) —
            "pool_timeout" | "local_protocol_error" | <ExceptionClassName>.
            error_type 만으로 구분 안 되는 내부 원인 식별. 대부분 None.
    """

    def __init__(
        self, message: str, status_code: int = 0, detail: str | None = None
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.message} ({self.status_code})" if self.status_code else self.message


class HandlerConnectionError(SDKError):
    """Handler 서비스 자체에 도달 불가 (네트워크 / handler down).

    status_code=0 — HTTP 응답을 받지 못함. caller 의 retry 대상이지만 handler
    자체 가용성이 회복돼야 의미 있음.
    """

    def __init__(self, message: str = "Handler 서비스에 연결할 수 없습니다") -> None:
        super().__init__(message, status_code=0)


class HandlerError(SDKError):
    """Handler 가 에러 응답을 반환 (base).

    실제 sub-class 가 dispatch 되므로 `except HandlerError` 는 모든 origin 을
    포괄. caller 가 origin 별 분기를 원하면 sub-class 또는 `error_type`
    문자열 사용.

    Attributes:
        error_type: 6개 값 중 하나
            ("validation" | "content_blocked" | "transport" | "provider"
            | "handler_ops" | "handler_internal")
            또는 None (구버전 handler 응답 호환).
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        error_type: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, status_code, detail)
        self.error_type = error_type

    def __str__(self) -> str:
        prefix = f"[{self.error_type}] " if self.error_type else ""
        return f"{prefix}{self.message} ({self.status_code})"

    @property
    def origin(self) -> str:
        """4 origin 으로 grouping — 운영 로그 / alert 라우팅 / 사용자 메시지용.

        Returns:
            "caller" | "transport" | "provider" | "handler" | "unknown"
        """
        return _ERROR_TYPE_TO_ORIGIN.get(self.error_type or "", "unknown")


# ─────────────────────────────────────────────────────────────
# caller 출처
# ─────────────────────────────────────────────────────────────


class ValidationError(HandlerError):
    """Caller 의 요청 형식 위반 (schema / 필수 필드 / 호환 안 되는 조합).

    재시도 무의미 — caller 가 요청 페이로드 수정 필요.
    """


class ContentBlockedError(HandlerError):
    """Gemini 가 caller content 를 차단 (SAFETY/RECITATION/MAX_TOKENS 등).

    재시도 무의미 — caller 가 prompt 또는 generation config 수정 필요.

    finish_reason 은 message 내 포함 (handler 가 detail 에 명시).
    """


# ─────────────────────────────────────────────────────────────
# transport 출처
# ─────────────────────────────────────────────────────────────


class TransportError(HandlerError):
    """전송 계층 에러 (upstream network/TLS/protocol/timeout).

    Caller 의 retry 정책에 따라 backoff 후 재시도 가능. handler 는 분류만 함.

    status_code:
        502 — Bad Gateway (connection 실패, RST, proxy 등)
        504 — Gateway Timeout (connect/read/write timeout)
    """


# ─────────────────────────────────────────────────────────────
# provider 출처
# ─────────────────────────────────────────────────────────────


class ProviderError(HandlerError):
    """Gemini API 도메인 에러 (4xx/5xx 응답, 빈 응답 등).

    status_code 는 Gemini status 보존. 429 는 retry-able, 5xx 는 transient
    가능성. Caller 의 retry 정책은 caller 책임.
    """


# ─────────────────────────────────────────────────────────────
# handler 출처
# ─────────────────────────────────────────────────────────────


class HandlerOpsError(HandlerError):
    """Handler 운영/구성 이슈 (API key 만료, quota 초과 등).

    운영팀 즉시 alert 대상. caller 의 자동 retry 는 무의미.
    """


class HandlerInternalError(HandlerError):
    """Handler 자체 버그 / 자원 한계 (코드 버그, pool 고갈 등).

    코드 작성자 alert 대상. caller 의 자동 retry 는 무의미.
    """


# ─────────────────────────────────────────────────────────────
# error_type → exception class dispatch (SDK `_request` 에서 사용)
# ─────────────────────────────────────────────────────────────


ERROR_TYPE_DISPATCH: dict[str, type[HandlerError]] = {
    "validation": ValidationError,
    "content_blocked": ContentBlockedError,
    "transport": TransportError,
    "provider": ProviderError,
    "handler_ops": HandlerOpsError,
    "handler_internal": HandlerInternalError,
}


_ERROR_TYPE_TO_ORIGIN: dict[str, str] = {
    "validation": "caller",
    "content_blocked": "caller",
    "transport": "transport",
    "provider": "provider",
    "handler_ops": "handler",
    "handler_internal": "handler",
    # 구버전 handler 호환
    "handler": "handler",
}


def from_error_type(
    message: str,
    status_code: int,
    error_type: str | None,
    detail: str | None = None,
) -> HandlerError:
    """error_type 으로 적절한 SDK exception sub-class 인스턴스 생성.

    Unknown / None error_type 은 HandlerError base 로 fallback —
    구버전 handler 응답 또는 dispatch table 누락 케이스 대응.

    detail 은 handler_internal 의 sub-cause slug (wire `detail` 필드) —
    downstream relay (예: llm-request) 가 운영 alert 라우팅에 사용.
    """
    cls = ERROR_TYPE_DISPATCH.get(error_type or "", HandlerError)
    return cls(message, status_code=status_code, error_type=error_type, detail=detail)
