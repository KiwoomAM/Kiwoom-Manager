"""rdb-manager async 통합 스모크 테스트.

실제 PostgreSQL이 필요하다. 환경변수 INTEGRATION=1 설정 시만 실행.
기본 실행에서는 skip.

실행 예:
    INTEGRATION=1 pytest tests/test_integration.py -v -s
"""
from __future__ import annotations

import asyncio
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="integration tests disabled (set INTEGRATION=1 to enable)",
)


async def test_async_check_connection_live():
    from rdb_manager.aio import check_database_connection

    assert await check_database_connection(verbose=True) is True


async def test_async_fetch_all_live():
    from rdb_manager.aio import fetch_all

    rows = await fetch_all("SELECT 1 AS n")
    assert rows == [(1,)]


async def test_async_fetch_all_dict_live():
    from rdb_manager.aio import fetch_all_dict

    rows = await fetch_all_dict("SELECT 1 AS n, 'x' AS s")
    assert rows == [{"n": 1, "s": "x"}]


async def test_async_concurrent_queries_do_not_block():
    """여러 async 쿼리를 동시 실행해 event loop가 블로킹되지 않음을 확인.

    pg_sleep(0.3)을 5개 동시 발행. 순차면 1.5초, 동시면 ~0.3초 근처.
    """
    import time

    from rdb_manager.aio import fetch_one

    async def one():
        return await fetch_one("SELECT pg_sleep(0.3)")

    start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(5)))
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, (
        f"동시 5건이 {elapsed:.2f}s 걸림 — event loop가 블로킹되는 것으로 의심"
    )
