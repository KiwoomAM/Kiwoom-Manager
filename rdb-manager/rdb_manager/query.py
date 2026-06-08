# -*- coding: utf-8 -*-
"""
rdb_manager/query.py

PostgreSQL 쿼리 실행 함수

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.
    예) "SELECT * FROM table WHERE id = %(id)s"
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
import pandas as pd

from rdb_handler.connection import get_connection
from rdb_handler.exceptions import QueryExecutionException

logger = logging.getLogger(__name__)


# ── SELECT 공통 헬퍼 ────────────────────────────────────────


def _execute_select(
    query: str,
    params: dict[str, Any] | None = None,
) -> tuple[list[str], list[tuple]]:
    """SELECT 쿼리 실행 후 (columns, rows) 반환하는 내부 헬퍼.

    PgBouncer 호환: 매 호출마다 새 커넥션을 할당하고 즉시 반환합니다.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(query, params or {})
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                return columns, rows
            except psycopg.Error as e:
                raise QueryExecutionException.from_psycopg(
                    e, sql=query, params=params, operation="SELECT",
                )


# ── SELECT 함수 ─────────────────────────────────────────────


def query_to_dataframe(
    query: str,
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    columns, rows = _execute_select(query, params)
    return pd.DataFrame(rows, columns=columns)


def fetch_all(
    query: str,
    params: dict[str, Any] | None = None,
) -> list[tuple]:
    _, rows = _execute_select(query, params)
    return rows


def fetch_all_dict(
    query: str,
    params: dict[str, Any] | None = None,
) -> list[dict]:
    columns, rows = _execute_select(query, params)
    return [dict(zip(columns, row)) for row in rows]


def fetch_one(
    query: str,
    params: dict[str, Any] | None = None,
) -> tuple | None:
    _, rows = _execute_select(query, params)
    return rows[0] if rows else None


# ── DML 공통 헬퍼 ──────────────────────────────────────────


def _execute_dml(
    sql: str,
    params: Any = None,
    *,
    many: bool = False,
) -> int:
    """DML 실행 후 영향받은 행 수를 반환하는 내부 헬퍼.

    PgBouncer 호환: 매 호출마다 새 커넥션을 할당하고 즉시 반환합니다.
    """
    operation = "BATCH_DML" if many else "DML"
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                if many:
                    cur.executemany(sql, params)
                else:
                    cur.execute(sql, params or {})
                rowcount = cur.rowcount
                conn.commit()
                return rowcount
            except psycopg.Error as e:
                conn.rollback()
                raise QueryExecutionException.from_psycopg(
                    e, sql=sql, params=params if not many else None,
                    operation=operation,
                )


# ── DML 함수 ────────────────────────────────────────────────


def execute_dml(
    sql: str,
    params: dict[str, Any] | None = None,
) -> int:
    return _execute_dml(sql, params)


def execute_many_dml(
    sql: str,
    params_list: list[dict[str, Any]],
) -> int:
    if not params_list:
        return 0
    return _execute_dml(sql, params_list, many=True)


def execute_insert(
    query: str,
    params: dict[str, Any] | None = None,
) -> int:
    return execute_dml(query, params)


def execute_update(
    query: str,
    params: dict[str, Any] | None = None,
) -> int:
    return execute_dml(query, params)


__all__ = [
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
]
