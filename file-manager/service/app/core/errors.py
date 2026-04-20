# -*- coding: utf-8 -*-
"""
File Manager Service 커스텀 예외

파일 서버 I/O 관련 예외(FileNotFoundOnServerError, FileStorageError)는
file_handler 패키지로 이전되었다.
"""


class FMServiceError(Exception):
    """File Manager Service 기본 예외"""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class MDBConnectionError(FMServiceError):
    """MDB Handler 연결 실패"""

    def __init__(self, message: str = "MDB Handler 연결 실패"):
        super().__init__(message, status_code=503)


class MetadataNotFoundError(FMServiceError):
    """파일 서버에는 파일이 있으나 MongoDB에 메타데이터가 없는 데이터 정합성 오류.

    정상 흐름에선 발생하지 않아야 한다 (업로드 시 반드시 MongoDB 기록이 생성됨).
    발생 시 수동 파일 배치나 MongoDB 롤백 같은 이상 상황을 조사해야 한다.
    """

    def __init__(self, file_path: str):
        super().__init__(
            f"메타데이터를 찾을 수 없습니다 (file_path={file_path})",
            status_code=500,
        )
