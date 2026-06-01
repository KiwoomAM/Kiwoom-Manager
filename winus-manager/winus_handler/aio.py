"""winus_handler.aio — 비동기 인터페이스.

사용법:
    from winus_handler.aio import query_to_dataframe

    df = await query_to_dataframe("SELECT * FROM table WHERE code = :code", {"code": "A001"})

Handler 접속 URL은 운영 서버(http://192.7.9.45:8200)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다.
"""

from typing import Any

import pandas as pd

from winus_handler._client import AsyncClient

_HANDLER_BASE_URL: str = "http://192.7.9.45:8200"

__all__ = [
    "configure",
    "close",
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    "search_index",
    "get_index_data",
    "check_database_connection",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    return _HANDLER_BASE_URL


def _get_client() -> AsyncClient:
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_HANDLER_BASE_URL)
    return _instance


async def close() -> None:
    """SDK 클라이언트 정리."""
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8200으로 고정됩니다.
    timeout만 적용됩니다.
    """
    global _instance
    _instance = AsyncClient(base_url=_HANDLER_BASE_URL, timeout=timeout)


# ── SELECT ──────────────────────────────────────────


async def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    return await _get_client().query_to_dataframe(query, params)


async def fetch_all(
    query: str, params: dict[str, Any] | None = None,
) -> list[tuple]:
    return await _get_client().fetch_all(query, params)


async def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None,
) -> list[dict]:
    return await _get_client().fetch_all_dict(query, params)


async def fetch_one(
    query: str, params: dict[str, Any] | None = None,
) -> tuple | None:
    return await _get_client().fetch_one(query, params)


# ── DML ────────────────────────────────────────────


async def execute_dml(
    sql: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_dml(sql, params)


async def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]],
) -> int:
    return await _get_client().execute_many_dml(sql, params_list)


async def execute_insert(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_insert(query, params)


async def execute_update(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_update(query, params)


# ── Index ──────────────────────────────────────────


async def search_index(keyword: str) -> list[dict]:
    """지수 검색. 한글↔영문 동의어, 정확 매칭 우선."""
    return await _get_client().search_index(keyword)


async def get_index_data(
    bm_code: str, start_date: str, end_date: str,
) -> "pd.DataFrame":
    """지수 일자별 데이터 → pandas DataFrame."""
    return await _get_client().get_index_data(bm_code, start_date, end_date)


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
