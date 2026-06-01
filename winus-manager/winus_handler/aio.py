"""winus_handler.aio — 비동기 인터페이스.

사용법:
    from winus_handler.aio import query_to_dataframe

    df = await query_to_dataframe("SELECT * FROM table WHERE code = :code", {"code": "A001"})
"""

import os
from typing import Any

import pandas as pd

from winus_handler._client import AsyncClient

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
    "check_database_connection",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    return os.getenv("WINUS_HANDLER_URL", "http://localhost:8200")


def _get_client() -> AsyncClient:
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


async def close() -> None:
    """SDK 클라이언트 정리."""
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정."""
    global _instance
    url = base_url or _resolve_base_url()
    _instance = AsyncClient(base_url=url, timeout=timeout)


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


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
