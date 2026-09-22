# -*- coding: utf-8 -*-
"""SDK 예외 정의"""


class VDBError(Exception):
    """VDB Handler SDK 최상위 예외."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class VDBConnectionError(VDBError):
    """VDB Handler Service에 연결할 수 없을 때."""

    def __init__(
        self, message: str = "VDB Handler Service에 연결할 수 없습니다"
    ) -> None:
        super().__init__(message, status_code=0)


class VDBHandlerError(VDBError):
    """서비스가 에러 응답을 반환했을 때."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message, status_code)


class VDBUnavailableError(VDBHandlerError):
    """서비스가 저장소에 닿지 못했을 때 (503).

    `vdb-handler`는 "닿지 못했다" 와 "닿았는데 실패했다" 를 다른 코드로 답한다. 앞은
    재시도가 의미 있고 뒤는 없다 — 그 구분을 만든 이유가 그것이다
    (`KiwoomAM/Kiwoom-Handler` 의 ADR 0003).

    `VDBHandlerError` 를 상속하므로 기존 `except VDBHandlerError` 는 그대로 잡고
    `status_code` 도 남는다. 재시도 여부를 타입으로 갈라 쓰려는 호출자만 이것을
    따로 잡는다.

    재시도는 하지 않는다. 언제 몇 번 다시 부를지는 호출자의 사정이고, SDK 는
    갈래를 알려 주는 데까지만 한다.
    """

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message, status_code)
