# -*- coding: utf-8 -*-
"""SDK 예외 정의"""


class EmbedderError(Exception):
    """Embedder Handler SDK 최상위 예외"""


class EmbedderConnectionError(EmbedderError):
    """Embedder Handler Service에 연결할 수 없을 때"""


class EmbedderHandlerError(EmbedderError):
    """서비스가 에러 응답을 반환했을 때"""

    def __init__(self, message: str, status_code: int = 0):
        self.status_code = status_code
        super().__init__(message)
