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
    """서비스가 지금 이 요청을 받지 못할 때 (503).

    `vdb-handler`는 "지금 받지 못한다"(503)와 "받아서 처리하다 실패했다"(500)를 다른
    코드로 답한다. 그 구분을 타입으로 올린 것이 이 예외다.

    503 에는 성격이 다른 것들이 섞인다. 저장소에 닿지 못한 경우, SPLADE·ColBERT 가
    아직 로딩 중인 경우, 그 모델이 아예 꺼져 있는 경우, 동시 처리 한계를 넘긴 경우다.
    그래서 이것을 "재시도하면 되는 오류" 로 읽으면 안 된다 — 꺼진 모델은 설정을 바꾸기
    전까지 계속 503 이라 재시도가 영영 통하지 않는다. 반대로 500 에도 다시 부르면 되는
    것이 섞인다(Gemini 의 일시 오류).

    `VDBHandlerError` 를 상속하므로 기존 `except VDBHandlerError` 는 그대로 잡고
    `status_code` 도 남는다.

    재시도는 SDK 가 하지 않는다. 무엇이 일시적인지는 부르는 쪽의 도메인이고, SDK 는
    상태를 갈라 주는 데까지만 한다.
    """

    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message, status_code)
