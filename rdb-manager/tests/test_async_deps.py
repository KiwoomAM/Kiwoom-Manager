"""rdb_manager.async_deps 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from .conftest import make_async_cursor, make_async_connection


async def test_get_pg_connection_yields_and_closes():
    cur = make_async_cursor()
    conn = make_async_connection(cur)

    with patch(
        "rdb_handler.async_connection.psycopg.AsyncConnection.connect",
        new=AsyncMock(return_value=conn),
    ):
        from rdb_manager.async_deps import get_pg_connection

        agen = get_pg_connection()
        yielded = await agen.__anext__()
        assert yielded is conn

        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass

    conn.close.assert_awaited_once()
