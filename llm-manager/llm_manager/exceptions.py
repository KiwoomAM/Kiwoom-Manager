"""표준화된 에러 타입.

서버 제거 후 manager 로직의 예외가 호출자에게 직접 전파된다. `status_code` /
`error_type` 는 서버 시절 HTTP 응답용이었으나, 현재는 호출자가 분기/로깅에 쓸 수
있는 메타데이터로 남겨둔다 (코드 내부에서는 읽지 않음).
"""


class LLMManagerError(Exception):
    """llm-manager 기본 에러."""

    def __init__(self, message: str, status_code: int = 500, error_type: str = "handler") -> None:
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(message)


class CacheError(LLMManagerError):
    """캐시 조회/저장 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="cache")


class FileUploadError(LLMManagerError):
    """파일 업로드 처리 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="file")


class ValidationError(LLMManagerError):
    """요청 검증 실패."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="validation")


class UnsupportedFileTypeError(LLMManagerError):
    """Gemini 지원 외 mime 타입.

    docs/07.inline-vs-files-api-strategy.md §2.4 화이트리스트에 없는 파일 타입이
    전달된 경우. resolve_files 진입 전 fail-fast로 차단.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="unsupported_file_type")


class FileTooLargeError(LLMManagerError):
    """개별 파일 2GB 초과.

    Gemini Files API 파일당 상한을 초과하여 inline·Files API 어느 경로로도
    처리 불가. docs/07.inline-vs-files-api-strategy.md §4.6 참고.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, error_type="file_too_large")


class CacheKeyNotFoundError(LLMManagerError):
    """cache_key 에 해당하는 캐시 문서가 없음 (fetch_by_cache_key).

    caller 가 잘못된/만료된 cache_key 로 조회한 경우. "서버 오류"가 아니라
    "그런 리소스 없음"이므로 not_found 로 명확히 신호한다.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404, error_type="not_found")


class FileResolveError(LLMManagerError):
    """stored_files resolve 단계에서 file-manager 가 파일을 찾을 수 없음.

    1차 get_file(file_path) 404 + 2차 check_cache(hashing 3 필드) miss →
    file-manager 에 정말로 없는 상태. 호출자가 catch 후 file-manager 의
    `register_or_get` 으로 새 file_path 를 받아 stored_files 로 재요청하는 것이
    권장 복구 경로.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502, error_type="file_resolve")


class PdfPageTooLargeError(LLMManagerError):
    """단일 PDF 페이지가 Gemini 한도(50MB)를 초과해 분할로도 전송 불가.

    페이지는 분할의 최소 단위라, 한 페이지가 50MB를 넘으면 어떤 분할로도 한도
    이하 파트를 만들 수 없다(모든 페이지가 ~50MB 공유 객체 풀을 참조하는 병리적
    PDF — 이슈 #21). docs/25.pdf-split-shared-pool-and-unsplittable-reject.md 참고.
    """

    def __init__(self, page: int, size_mb: float) -> None:
        self.page = page
        self.size_mb = size_mb
        super().__init__(
            f"이 PDF는 처리할 수 없습니다 — 단일 페이지가 Gemini 한도(50MB)를 "
            f"초과합니다 (p.{page}, {size_mb:.1f}MB).",
            status_code=422,
            error_type="pdf_page_too_large",
        )
