"""rdb_manager.async_query 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from rdb_handler.exceptions import QueryExecutionException

from .conftest import make_async_connection, make_async_cursor


def _patched_connect(cursor: AsyncMock):
    """psycopg.AsyncConnection.connect 모킹 (async_connection 모듈 경유)."""
    conn = make_async_connection(cursor)
    return patch(
        "rdb_handler.async_connection.psycopg.AsyncConnection.connect",
        new=AsyncMock(return_value=conn),
    ), conn


async def test_fetch_all_returns_rows():
    cur = make_async_cursor(
        description=[("id",), ("name",)],
        fetchall_rows=[(1, "a"), (2, "b")],
    )
    patcher, conn = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import fetch_all

        rows = await fetch_all("SELECT id, name FROM t")

    assert rows == [(1, "a"), (2, "b")]
    cur.execute.assert_awaited_once_with("SELECT id, name FROM t", {})
    conn.close.assert_awaited_once()


async def test_fetch_all_dict_maps_columns():
    cur = make_async_cursor(
        description=[("id",), ("name",)],
        fetchall_rows=[(1, "a"), (2, "b")],
    )
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import fetch_all_dict

        rows = await fetch_all_dict("SELECT id, name FROM t")

    assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


async def test_fetch_one_returns_first_row():
    cur = make_async_cursor(
        description=[("n",)],
        fetchall_rows=[(42,), (43,)],
    )
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import fetch_one

        row = await fetch_one("SELECT n FROM t ORDER BY n")

    assert row == (42,)


async def test_fetch_one_returns_none_when_empty():
    cur = make_async_cursor(description=[("n",)], fetchall_rows=[])
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import fetch_one

        row = await fetch_one("SELECT n FROM t WHERE 1=0")

    assert row is None


async def test_select_wraps_psycopg_error():
    cur = make_async_cursor(
        description=[("n",)],
        execute_side_effect=psycopg.errors.UndefinedTable("relation does not exist"),
    )
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import fetch_all

        with pytest.raises(QueryExecutionException):
            await fetch_all("SELECT * FROM nope")


async def test_query_to_dataframe_returns_dataframe():
    import pandas as pd

    cur = make_async_cursor(
        description=[("id",), ("val",)],
        fetchall_rows=[(1, 10.5), (2, 20.0)],
    )
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import query_to_dataframe

        df = await query_to_dataframe("SELECT id, val FROM t")

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "val"]
    assert len(df) == 2
    assert df.iloc[1]["val"] == 20.0


async def test_execute_dml_commits_on_success():
    cur = make_async_cursor(rowcount=3)
    patcher, conn = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import execute_dml

        affected = await execute_dml("UPDATE t SET a=1", {"x": 1})

    assert affected == 3
    cur.execute.assert_awaited_once_with("UPDATE t SET a=1", {"x": 1})
    conn.commit.assert_awaited_once()
    conn.rollback.assert_not_awaited()


async def test_execute_dml_rolls_back_on_error():
    cur = make_async_cursor(
        execute_side_effect=psycopg.errors.UniqueViolation("dup"),
    )
    patcher, conn = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import execute_dml

        with pytest.raises(QueryExecutionException):
            await execute_dml("INSERT INTO t VALUES (1)")

    conn.rollback.assert_awaited_once()
    conn.commit.assert_not_awaited()


async def test_execute_many_dml_empty_returns_zero():
    from rdb_manager.async_query import execute_many_dml

    assert await execute_many_dml("INSERT INTO t VALUES (%(v)s)", []) == 0


async def test_execute_many_dml_uses_executemany():
    cur = make_async_cursor(rowcount=2)
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import execute_many_dml

        affected = await execute_many_dml(
            "INSERT INTO t VALUES (%(v)s)",
            [{"v": 1}, {"v": 2}],
        )

    assert affected == 2
    cur.executemany.assert_awaited_once_with(
        "INSERT INTO t VALUES (%(v)s)", [{"v": 1}, {"v": 2}]
    )


async def test_execute_insert_update_delegate_to_execute_dml():
    cur = make_async_cursor(rowcount=1)
    patcher, _ = _patched_connect(cur)
    with patcher:
        from rdb_manager.async_query import execute_insert

        assert await execute_insert("INSERT INTO t VALUES (1)") == 1

    cur2 = make_async_cursor(rowcount=5)
    patcher2, _ = _patched_connect(cur2)
    with patcher2:
        from rdb_manager.async_query import execute_update

        assert await execute_update("UPDATE t SET a=1") == 5
