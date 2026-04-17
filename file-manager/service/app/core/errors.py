# -*- coding: utf-8 -*-
"""
File Manager Service 커스텀 예외
"""


class FMServiceError(Exception):
    """File Manager Service 기본 예외"""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class FileNotFoundOnServerError(FMServiceError):
    """File Server에서 파일을 찾을 수 없는 경우"""

    def __init__(self, message: str = "파일을 찾을 수 없습니다"):
        super().__init__(message, status_code=404)


class FileStorageError(FMServiceError):
    """File Server 저장/조회 실패"""

    def __init__(self, message: str = "파일 저장소 오류"):
        super().__init__(message, status_code=500)


class MDBConnectionError(FMServiceError):
    """MDB Handler 연결 실패"""

    def __init__(self, message: str = "MDB Handler 연결 실패"):
        super().__init__(message, status_code=503)
