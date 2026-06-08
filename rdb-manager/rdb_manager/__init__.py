# -*- coding: utf-8 -*-
"""
rdb_manager

PostgreSQL 공개 클라이언트 (PgBouncer 경유). 저수준 연결은 rdb_handler에 의존.

사용법:
    from rdb_manager import query_to_dataframe
    df = query_to_dataframe("SELECT * FROM t WHERE id = %(id)s", {"id": 1})

    from rdb_manager import fetch_all_dict
    rows = fetch_all_dict("SELECT * FROM t")

    from rdb_manager import execute_dml
    execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

    from rdb_manager import get_connection         # rdb_handler 재export
    from rdb_manager import get_pg_connection      # FastAPI Depends
    from rdb_manager import PostgresConfig         # rdb_handler 재export

    import rdb_manager.aio as aio                  # async API
"""

# ── 저수준 재export (rdb_handler) ──────────────────────────
from rdb_handler import (
    PostgresConfig,
    get_connection,
    check_database_connection,
    DatabaseException,
    ConnectionPoolException,
    QueryExecutionException,
)

# ── Query (manager local) ─────────────────────────────────
from .query import (
    query_to_dataframe,
    fetch_all,
    fetch_all_dict,
    fetch_one,
    execute_dml,
    execute_many_dml,
    execute_insert,
    execute_update,
)

# ── FastAPI Deps (manager local) ──────────────────────────
from .deps import (
    get_pg_connection,
)


__all__ = [
    # Config
    "PostgresConfig",
    # Connection
    "get_connection",
    "check_database_connection",
    # Query
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    # FastAPI Deps
    "get_pg_connection",
    # Exceptions
    "DatabaseException",
    "ConnectionPoolException",
    "QueryExecutionException",
]
