# -*- coding: utf-8 -*-
"""File Manager SDK 예외 클래스."""


class FMError(Exception):
    """File Manager SDK 기본 에러."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class FMConnectionError(FMError):
    """FM Service 연결 실패."""

    def __init__(
        self, message: str = "File Manager Service에 연결할 수 없습니다"
    ) -> None:
        super().__init__(message, status_code=0)


class FMServiceError(FMError):
    """FM Service가 에러 응답을 반환."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message, status_code)


class MetadataNotFoundError(FMError):
    """파일 서버에는 파일이 있으나 MongoDB에 메타데이터가 없는 데이터 정합성 오류.

    정상 흐름에선 발생하지 않아야 한다(업로드 시 반드시 메타데이터가 기록됨).
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(
            f"메타데이터를 찾을 수 없습니다 (file_path={file_path})",
            status_code=500,
        )


class MDBConnectionError(FMError):
    """MDB Handler(메타데이터 저장소) 연결/응답 오류."""

    def __init__(self, message: str = "MDB Handler 연결 실패") -> None:
        super().__init__(message, status_code=503)
