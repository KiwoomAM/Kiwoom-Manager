# -*- coding: utf-8 -*-
"""
rdb_manager/async_query.py

PostgreSQL 비동기 쿼리 실행 함수.

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import psycopg

from rdb_handler.async_connection import get_connection
from rdb_handler.exceptions import QueryExecutionException

logger = logging.getLogger(__name__)


# ── SELECT 공통 헬퍼 ─────────────────────────────────────────


async def _execute_select(
    query: str,
    params: dict[str, Any] | None = None,
) -> tuple[list[str], list[tuple]]:
    """비동기 SELECT 실행 후 (columns, rows) 반환."""
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(query, params or {})
                columns = [desc[0] for desc in cur.description]
                rows = await cur.fetchall()
                return columns, rows
            except psycopg.Error as e:
                raise QueryExecutionException.from_psycopg(
                    e, sql=query, params=params, operation="SELECT",
                )


# ── SELECT 공개 API ─────────────────────────────────────────


async def fetch_all(
    query: str,
    params: dict[str, Any] | None = None,
) -> list[tuple]:
    _, rows = await _execute_select(query, params)
    return rows


async def fetch_all_dict(
    query: str,
    params: dict[str, Any] | None = None,
) -> list[dict]:
    columns, rows = await _execute_select(query, params)
    return [dict(zip(columns, row)) for row in rows]


async def fetch_one(
    query: str,
    params: dict[str, Any] | None = None,
) -> tuple | None:
    _, rows = await _execute_select(query, params)
    return rows[0] if rows else None


async def query_to_dataframe(
    query: str,
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    columns, rows = await _execute_select(query, params)
    return pd.DataFrame(rows, columns=columns)


# ── DML 공통 헬퍼 ──────────────────────────────────────────


async def _execute_dml(
    sql: str,
    params: Any = None,
    *,
    many: bool = False,
) -> int:
    """비동기 DML 실행 후 영향받은 행 수를 반환."""
    operation = "BATCH_DML" if many else "DML"
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            try:
                if many:
                    await cur.executemany(sql, params)
                else:
                    await cur.execute(sql, params or {})
                rowcount = cur.rowcount
                await conn.commit()
                return rowcount
            except psycopg.Error as e:
                await conn.rollback()
                raise QueryExecutionException.from_psycopg(
                    e, sql=sql, params=params if not many else None,
                    operation=operation,
                )


# ── DML 공개 API ────────────────────────────────────────────


async def execute_dml(
    sql: str,
    params: dict[str, Any] | None = None,
) -> int:
    return await _execute_dml(sql, params)


async def execute_many_dml(
    sql: str,
    params_list: list[dict[str, Any]],
) -> int:
    if not params_list:
        return 0
    return await _execute_dml(sql, params_list, many=True)


async def execute_insert(
    query: str,
    params: dict[str, Any] | None = None,
) -> int:
    return await execute_dml(query, params)


async def execute_update(
    query: str,
    params: dict[str, Any] | None = None,
) -> int:
    return await execute_dml(query, params)


__all__ = [
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "query_to_dataframe",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
]
