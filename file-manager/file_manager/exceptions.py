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
    """주어진 file_path에 해당하는 메타데이터가 MongoDB에 없음.

    두 가지 상황을 함께 가리킨다.
      - 조회 미스 — stale하거나 오타 난 file_path로 `get_metadata`/`get_file`을 부른 경우.
        `get_metadata`가 임의 경로를 받는 공개 lookup이라 **정상 흐름에서도** 나온다.
      - 데이터 정합성 오류 — 파일 서버엔 파일이 있는데 메타만 없는 경우(업로드 시 메타가
        반드시 기록되므로 드물다).

    둘을 구분하지 않고 4xx로 둔다 — 흔한 쪽이 조회 미스이고, 5xx로 두면 소비처가
    `status_code >= 500`에 건 알림·재시도가 단순 미스마다 울린다. 재시도로 복구되지
    않는다는 점은 양쪽이 같다(소비처는 `isinstance`로 판정한다).
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(
            f"메타데이터를 찾을 수 없습니다 (file_path={file_path})",
            status_code=404,
        )


class MDBConnectionError(FMError):
    """MDB Handler(메타데이터 저장소) 연결/응답 오류."""

    def __init__(self, message: str = "MDB Handler 연결 실패") -> None:
        super().__init__(message, status_code=503)


class FMConfigError(FMError):
    """SDK 동작에 필요한 설정 누락.

    런타임 장애가 아니라 배포 설정 실수다(예: 업로드 시 SERVICE_NAME 부재).
    호출자가 재시도로 복구할 수 없으므로 잡지 말고 터뜨리는 것이 정상이다.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500)
