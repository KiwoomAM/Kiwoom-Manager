"""rdb_manager.aio 공개 API 재export 검증."""
from __future__ import annotations

import inspect


def test_aio_exposes_all_public_symbols():
    import rdb_manager.aio as aio

    expected = {
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
    }

    assert expected.issubset(set(aio.__all__))
    for name in expected:
        assert hasattr(aio, name), f"aio에 {name}이 없음"


def test_aio_query_functions_are_coroutine_functions():
    """SELECT/DML async 함수들이 실제 coroutine function인지 검증."""
    import rdb_manager.aio as aio

    for name in [
        "fetch_all", "fetch_all_dict", "fetch_one", "query_to_dataframe",
        "execute_dml", "execute_many_dml", "execute_insert", "execute_update",
        "check_database_connection", "get_pg_connection",
    ]:
        fn = getattr(aio, name)
        assert inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn), (
            f"{name}이 coroutine/async generator가 아님"
        )


def test_sync_api_untouched():
    """기존 sync 공개 API가 그대로 유지되는지 회귀 확인."""
    import rdb_manager

    for name in [
        "get_connection", "check_database_connection",
        "fetch_all", "fetch_all_dict", "fetch_one", "query_to_dataframe",
        "execute_dml", "execute_many_dml", "execute_insert", "execute_update",
        "get_pg_connection",
    ]:
        assert hasattr(rdb_manager, name), f"sync API에서 {name} 누락됨"
