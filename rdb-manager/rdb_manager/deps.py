# -*- coding: utf-8 -*-
"""
rdb_manager/deps.py

FastAPI 의존성 주입 모듈 (PostgreSQL via PgBouncer)

사용법:
    from fastapi import Depends
    from rdb_manager.deps import get_pg_connection

    @router.get("/example")
    def example(conn = Depends(get_pg_connection)):
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            ...
"""

from typing import Generator

from rdb_handler.connection import get_connection


def get_pg_connection() -> Generator:
    with get_connection() as conn:
        yield conn


__all__ = [
    "get_pg_connection",
]
