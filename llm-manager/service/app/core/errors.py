"""표준화된 에러 타입."""


class LLMRequestServiceError(Exception):
    """llm-request Service 기본 에러."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "handler") -> None:
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


class CacheError(LLMRequestServiceError):
    """캐시 조회/저장 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="cache")


class FileUploadError(LLMRequestServiceError):
    """파일 업로드 처리 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="file")


class ValidationError(LLMRequestServiceError):
    """요청 검증 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="validation")


class UnsupportedFileTypeError(LLMRequestServiceError):
    """Gemini 지원 외 mime 타입.

    docs/inline-vs-files-api-strategy.md §2.4 화이트리스트에 없는 파일 타입이
    전달된 경우. resolve_files 진입 전 fail-fast로 차단.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="unsupported_file_type")


class FileTooLargeError(LLMRequestServiceError):
    """개별 파일 2GB 초과.

    Gemini Files API 파일당 상한을 초과하여 inline·Files API 어느 경로로도
    처리 불가. docs/inline-vs-files-api-strategy.md §4.6 참고.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="file_too_large")
