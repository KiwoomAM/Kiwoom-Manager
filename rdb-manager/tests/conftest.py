"""rdb-manager 테스트 공용 fixture.

psycopg.AsyncConnection 및 AsyncCursor를 모킹하기 위한 헬퍼.
실제 DB 없이 async 로직 경로를 검증한다.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_async_cursor(
    *,
    description: list[tuple] | None = None,
    fetchall_rows: list[tuple] | None = None,
    fetchone_row: tuple | None = None,
    rowcount: int = 0,
    execute_side_effect: Exception | None = None,
) -> AsyncMock:
    """AsyncCursor 모의 객체 생성.

    `async with conn.cursor() as cur:` 형태로 쓸 수 있도록
    __aenter__/__aexit__도 설정한다.
    """
    cur = AsyncMock()
    cur.description = description
    cur.rowcount = rowcount
    cur.execute = AsyncMock(side_effect=execute_side_effect)
    cur.executemany = AsyncMock(side_effect=execute_side_effect)
    cur.fetchall = AsyncMock(return_value=fetchall_rows or [])
    cur.fetchone = AsyncMock(return_value=fetchone_row)
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=False)
    return cur


def make_async_connection(cursor: AsyncMock) -> AsyncMock:
    """AsyncConnection 모의 객체 생성.

    conn.cursor()는 동기 호출이지만 반환된 객체는 async context manager.
    commit/rollback/close는 async 메서드.
    """
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=cursor)
    conn.commit = AsyncMock()
    conn.rollback = AsyncMock()
    conn.close = AsyncMock()
    return conn


@pytest.fixture
def mock_cursor() -> AsyncMock:
    """기본 모의 cursor."""
    return make_async_cursor()


@pytest.fixture
def mock_connection(mock_cursor: AsyncMock) -> AsyncMock:
    """기본 모의 connection."""
    return make_async_connection(mock_cursor)


@pytest.fixture(autouse=True)
def _configure_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """모든 테스트에서 PostgresConfig가 configured 상태인 것처럼 동작하도록.

    integration 테스트(INTEGRATION=1)에서는 실제 .env 설정을 그대로 쓰고 싶으므로
    해당 경우에는 override를 skip.
    """
    if os.environ.get("INTEGRATION") == "1":
        return

    from rdb_handler.config import PostgresConfig

    monkeypatch.setattr(PostgresConfig, "PG_HOST", "localhost")
    monkeypatch.setattr(PostgresConfig, "PG_PORT", "5432")
    monkeypatch.setattr(PostgresConfig, "PG_DATABASE", "testdb")
    monkeypatch.setattr(PostgresConfig, "PG_USER", "tester")
    monkeypatch.setattr(PostgresConfig, "PG_PASSWORD", "secret")
    monkeypatch.setattr(PostgresConfig, "CONNECT_TIMEOUT", 5)
    # is_configured는 구현에 의존하지 않도록 직접 True 반환하도록 override.
    # 특정 테스트(not_configured 경로)에서 다시 override할 수 있음.
    monkeypatch.setattr(
        PostgresConfig, "is_configured", classmethod(lambda cls: True)
    )
    monkeypatch.setattr(
        PostgresConfig, "get_dsn", classmethod(lambda cls: "postgresql://tester:secret@localhost:5432/testdb")
    )
    monkeypatch.setattr(
        PostgresConfig, "get_env_path", classmethod(lambda cls: "/tmp/.env.test")
    )
