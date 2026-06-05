# -*- coding: utf-8 -*-
"""
rdb_manager.aio — 비동기 인터페이스.

sync API와 1:1 대응되는 async API를 제공한다.
함수 이름·시그니처·반환 타입은 sync와 동일, import path로만 구분.

사용법:
    # async SELECT
    from rdb_manager.aio import fetch_all_dict
    rows = await fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})

    # async DML
    from rdb_manager.aio import execute_dml
    await execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

    # async Connection (직접 사용)
    from rdb_manager.aio import get_connection
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")

    # FastAPI 비동기 의존성
    from fastapi import Depends
    from rdb_manager.aio import get_pg_connection

    @router.get("/example")
    async def example(conn = Depends(get_pg_connection)):
        ...
"""
from rdb_handler.async_connection import (
    get_connection,
    check_database_connection,
)
from .async_query import (
    query_to_dataframe,
    fetch_all,
    fetch_all_dict,
    fetch_one,
    execute_dml,
    execute_many_dml,
    execute_insert,
    execute_update,
)
from .async_deps import (
    get_pg_connection,
)

__all__ = [
    "get_connection",
    "check_database_connection",
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    "get_pg_connection",
]
