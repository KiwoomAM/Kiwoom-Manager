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
