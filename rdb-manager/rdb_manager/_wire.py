"""동기·비동기 클라이언트가 함께 쓰는 와이어 규칙 — 접근 키와 요청이 고르는 database.

두 클라이언트가 각자 들고 있으면 한쪽만 고쳐지는 날 `rdb_manager` 와 `rdb_manager.aio` 가 다르게
요청한다. 접근 키의 뜻은 레포 루트 CONTEXT.md 「auth_key」를 따른다.
"""

import logging
from typing import Any

from rdb_manager._config import _ADMIN_KEY
from rdb_manager.exceptions import HandlerError

logger = logging.getLogger(__name__)

# 핸들러가 접근 등급으로 거절했다는 상태코드. 전용 예외는 두지 않는다 — 소비자가 다르게 처리할
# 일이 없고, 임시 안전장치를 걷어낼 때 지울 공개 이름을 남기지 않기 위해서다.
_ACCESS_REJECTED = frozenset({401, 403})


def access_headers(auth_key: str | None) -> dict[str, str]:
    """요청마다 싣는 접근 키 헤더.

    Args:
        auth_key: 소비자가 `configure(auth_key=)` 로 고른 키. 비어 있으면 admin 키를 싣는다.

    Returns:
        `Authorization: Bearer <키>` 헤더.
    """
    return {"Authorization": f"Bearer {auth_key or _ADMIN_KEY}"}


def log_check_failure(error: Exception) -> None:
    """연결 점검이 False 로 삼키는 실패를 남긴다.

    접근 거절은 키가 어긋난 것이 DB 가 끊긴 것으로 읽히지 않게 ERROR 로 따로 남긴다.

    Args:
        error: 연결 점검 중에 난 예외.
    """
    if isinstance(error, HandlerError) and error.status_code in _ACCESS_REJECTED:
        logger.error(
            "[RDB 핸들러] DB 연결 확인 거절됨 (HTTP %d: %s) — DB 연결 실패가 아니라 접근 키·등급 문제",
            error.status_code,
            error,
        )
    else:
        logger.warning("[RDB 핸들러] DB 연결 확인 실패: %s", error)


def with_database(body: dict[str, Any], database: str | None) -> dict[str, Any]:
    """요청이 고른 database 를 본문에 싣는다.

    고르지 않으면 필드를 싣지 않는다 — 기본 database 와 허용 목록은 핸들러 한 곳이 정한다.

    Args:
        body: 요청 본문.
        database: 요청이 고른 database. 없으면 핸들러의 기본 database.

    Returns:
        database 를 실은(또는 그대로인) 본문.
    """
    return body if database is None else {**body, "database": database}
