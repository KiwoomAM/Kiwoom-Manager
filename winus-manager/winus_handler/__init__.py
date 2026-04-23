"""winus_handler — 동기 인터페이스.

사용법:
    import winus_handler

    winus_handler.configure(base_url="http://winus-handler:8200")
    df = winus_handler.query_to_dataframe("SELECT * FROM table WHERE code = :code", {"code": "A001"})

환경변수 WINUS_HANDLER_URL 설정 시 configure() 없이도 사용 가능:
    from winus_handler import query_to_dataframe
    df = query_to_dataframe("SELECT * FROM table")
"""

import os
from typing import Any

import pandas as pd

from winus_handler._sync_client import SyncClient
from winus_handler.exceptions import (
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
    # API — Index
    "search_index",
    "list_all_indexes",
    "get_index_data",
    "get_index_data_multi",
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
    return os.getenv("WINUS_HANDLER_URL", "http://localhost:8200")


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

    Args:
        base_url: Handler 서비스 URL (기본: 환경변수 또는 localhost:8200)
        timeout: HTTP 타임아웃 (초)
    """
    global _instance
    url = base_url or _resolve_base_url()
    _instance = SyncClient(base_url=url, timeout=timeout)


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


# ── Index ──────────────────────────────────────────


def search_index(keyword: str) -> list[dict]:
    """지수 검색. 한글↔영문 동의어, 정확 매칭 우선."""
    return _get_client().search_index(keyword)


def list_all_indexes() -> list[dict]:
    """전체 지수 목록 조회 (DW_내부코드 MK002, 사용여부='Y')."""
    return _get_client().list_all_indexes()


def get_index_data(
    bm_code: str, start_date: str, end_date: str,
) -> pd.DataFrame:
    """지수 일자별 데이터 → pandas DataFrame."""
    return _get_client().get_index_data(bm_code, start_date, end_date)


def get_index_data_multi(
    codes: list[str], start_date: str, end_date: str,
) -> pd.DataFrame:
    """다중 지수 일자별 데이터 → pandas DataFrame (wide format)."""
    return _get_client().get_index_data_multi(codes, start_date, end_date)


# ── Health ─────────────────────────────────────────


def check_database_connection() -> bool:
    """DB 연결 상태 확인."""
    return _get_client().check_database_connection()
