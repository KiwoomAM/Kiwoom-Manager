# -*- coding: utf-8 -*-
"""
rdb_manager/async_deps.py

FastAPI 비동기 의존성 주입 모듈 (PostgreSQL via PgBouncer).

사용법:
    from fastapi import Depends
    from rdb_manager.aio import get_pg_connection

    @router.get("/example")
    async def example(conn = Depends(get_pg_connection)):
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            ...
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import psycopg

from rdb_handler.async_connection import get_connection


async def get_pg_connection() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    async with get_connection() as conn:
        yield conn


__all__ = [
    "get_pg_connection",
]
