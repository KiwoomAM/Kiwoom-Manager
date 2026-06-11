# -*- coding: utf-8 -*-
"""rdb_manager — 동기 인터페이스.

PostgreSQL 공개 클라이언트. rdb-handler 서비스(HTTP)에 요청을 보낸다.

사용법:
    import rdb_manager

    df = rdb_manager.query_to_dataframe(
        "SELECT * FROM t WHERE id = %(id)s", {"id": 1}
    )
    rows = rdb_manager.fetch_all_dict("SELECT * FROM t")
    rdb_manager.execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

    import rdb_manager.aio as aio   # async API

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.

Handler 접속 URL은 운영 서버(http://192.7.9.45:8700)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다. timeout 등 기타
설정은 configure()로 조정 가능합니다.
"""

from typing import Any

import pandas as pd

from rdb_manager._config import HANDLER_BASE_URL
from rdb_manager._sync_client import SyncClient
from rdb_manager.exceptions import (
    ConnectionPoolException,
    DatabaseException,
    HandlerConnectionError,
    HandlerError,
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
    "HandlerError",
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


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8700으로 고정됩니다.
    timeout만 적용됩니다.
    """
    global _instance
    _instance = SyncClient(base_url=_resolve_base_url(), timeout=timeout)


# ── SELECT ──────────────────────────────────────────


def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """SELECT → pandas DataFrame."""
    return _get_client().query_to_dataframe(query, params)


def fetch_all(
    query: str, params: dict[str, Any] | None = None,
) -> list[tuple]:
    """SELECT → list of tuples."""
    return _get_client().fetch_all(query, params)


def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None,
) -> list[dict]:
    """SELECT → list of dicts."""
    return _get_client().fetch_all_dict(query, params)


def fetch_one(
    query: str, params: dict[str, Any] | None = None,
) -> tuple | None:
    """SELECT → 첫 번째 row 또는 None."""
    return _get_client().fetch_one(query, params)


# ── DML ────────────────────────────────────────────


def execute_dml(
    sql: str, params: dict[str, Any] | None = None,
) -> int:
    """일반 DML 실행."""
    return _get_client().execute_dml(sql, params)


def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]],
) -> int:
    """배치 DML 실행."""
    return _get_client().execute_many_dml(sql, params_list)


def execute_insert(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    """INSERT 전용."""
    return _get_client().execute_insert(query, params)


def execute_update(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    """UPDATE 전용."""
    return _get_client().execute_update(query, params)


# ── Health ─────────────────────────────────────────


def check_database_connection() -> bool:
    """DB 연결 상태 확인."""
    return _get_client().check_database_connection()
