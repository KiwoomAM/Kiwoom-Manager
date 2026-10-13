"""winus_manager — 동기 인터페이스.

사용법:
    import winus_manager

    df = winus_manager.query_to_dataframe("SELECT * FROM table WHERE code = :code", {"code": "A001"})

Handler 접속 URL은 운영 서버(http://192.7.9.45:8200)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다. timeout 등 기타
설정은 configure()로 조정 가능합니다.

조회마다 이름(client_query_id)과 마감(query_timeout_ms)을 keyword 로 붙일 수 있습니다.
뜻은 CONTEXT.md, 쓰는 법은 README.md 를 따릅니다.
"""

from typing import Any

import pandas as pd

from winus_manager._config import HANDLER_BASE_URL
from winus_manager._sync_client import SyncClient
from winus_manager.exceptions import (
    ConnectionPoolException,
    ConsumerTimeoutError,
    DatabaseException,
    HandlerConnectionError,
    HandlerError,
    QueryCancelledError,
    QueryExecutionException,
    SDKError,
)

__all__ = [
    # API — SELECT
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    # API — DML
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    # API — Health
    "check_database_connection",
    # Config / Lifecycle
    "configure",
    "close",
    # Exceptions (기존 호환)
    "DatabaseException",
    "ConnectionPoolException",
    "QueryExecutionException",
    # Exceptions (SDK)
    "SDKError",
    "HandlerConnectionError",
    "ConsumerTimeoutError",
    "HandlerError",
    "QueryCancelledError",
]

_instance: SyncClient | None = None


def _resolve_base_url() -> str:
    return HANDLER_BASE_URL


def _get_client() -> SyncClient:
    global _instance
    if _instance is None:
        _instance = SyncClient(base_url=_resolve_base_url())
    return _instance


def close() -> None:
    """SDK 클라이언트 정리. 프로세스 종료 전에 호출하면 리소스를 깔끔하게 해제한다."""
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None


def configure(
    base_url: str | None = None, timeout: float = 30.0, auth_key: str | None = None,
) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8200으로 고정됩니다.
    timeout 과 auth_key 가 적용됩니다.

    auth_key 는 핸들러의 접근 등급을 정하는 키입니다(레포 루트 CONTEXT.md 「auth_key」).
    주지 않으면 admin 키를 싣습니다(빈 문자열은 그대로 실려 401 로 거절됩니다). 기본값으로 두고,
    guest 동작을 시험할 때만 바꿉니다.
    """
    global _instance
    if _instance is not None:
        _instance.close()
    _instance = SyncClient(base_url=_resolve_base_url(), timeout=timeout, auth_key=auth_key)


# ── SELECT ──────────────────────────────────────────


def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> pd.DataFrame:
    """SELECT → pandas DataFrame."""
    return _get_client().query_to_dataframe(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def fetch_all(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> list[tuple]:
    """SELECT → list of tuples."""
    return _get_client().fetch_all(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> list[dict]:
    """SELECT → list of dicts."""
    return _get_client().fetch_all_dict(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def fetch_one(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> tuple | None:
    """SELECT → 첫 번째 row 또는 None."""
    return _get_client().fetch_one(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


# ── DML ────────────────────────────────────────────


def execute_dml(
    sql: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    """일반 DML 실행."""
    return _get_client().execute_dml(
        sql, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]], *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    """배치 DML 실행."""
    return _get_client().execute_many_dml(
        sql, params_list,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def execute_insert(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    """INSERT 전용."""
    return _get_client().execute_insert(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


def execute_update(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    """UPDATE 전용."""
    return _get_client().execute_update(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


# ── Health ─────────────────────────────────────────


def check_database_connection() -> bool:
    """DB 연결 상태 확인."""
    return _get_client().check_database_connection()
