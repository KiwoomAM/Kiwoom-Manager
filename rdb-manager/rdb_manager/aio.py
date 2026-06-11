# -*- coding: utf-8 -*-
"""rdb_manager.aio — 비동기 인터페이스.

sync API와 1:1 대응되는 async API. 함수 이름·시그니처·반환 타입은 sync와
동일, import path로만 구분.

사용법:
    from rdb_manager.aio import fetch_all_dict
    rows = await fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})

    from rdb_manager.aio import execute_dml
    await execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "x"})

참고: PostgreSQL 바인드 변수 형식은 %(name)s 입니다.

Handler 접속 URL은 운영 서버(http://192.7.9.45:8700)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다.
"""

from typing import Any

import pandas as pd

from rdb_manager._client import AsyncClient
from rdb_manager._config import HANDLER_BASE_URL

__all__ = [
    "configure",
    "close",
    "query_to_dataframe",
    "fetch_all",
    "fetch_all_dict",
    "fetch_one",
    "execute_dml",
    "execute_many_dml",
    "execute_insert",
    "execute_update",
    "check_database_connection",
]

_instance: AsyncClient | None = None


def _resolve_base_url() -> str:
    return HANDLER_BASE_URL


def _get_client() -> AsyncClient:
    global _instance
    if _instance is None:
        _instance = AsyncClient(base_url=_resolve_base_url())
    return _instance


async def close() -> None:
    """SDK 클라이언트 정리. 애플리케이션 종료 또는 FastAPI lifespan 종료 시 호출해야 한다."""
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


def configure(base_url: str | None = None, timeout: float = 30.0) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8700으로 고정됩니다.
    timeout만 적용됩니다.

    주의: 동기 함수이므로 이전 인스턴스를 await close()할 수 없다. 이미
    인스턴스가 생성된 뒤 재설정하려면 먼저 `await close()`를 호출해 기존
    연결 풀을 정리한 다음 configure()를 호출하라 (풀 누수 방지).
    """
    global _instance
    _instance = AsyncClient(base_url=_resolve_base_url(), timeout=timeout)


# ── SELECT ──────────────────────────────────────────


async def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    return await _get_client().query_to_dataframe(query, params)


async def fetch_all(
    query: str, params: dict[str, Any] | None = None,
) -> list[tuple]:
    return await _get_client().fetch_all(query, params)


async def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None,
) -> list[dict]:
    return await _get_client().fetch_all_dict(query, params)


async def fetch_one(
    query: str, params: dict[str, Any] | None = None,
) -> tuple | None:
    return await _get_client().fetch_one(query, params)


# ── DML ────────────────────────────────────────────


async def execute_dml(
    sql: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_dml(sql, params)


async def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]],
) -> int:
    return await _get_client().execute_many_dml(sql, params_list)


async def execute_insert(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_insert(query, params)


async def execute_update(
    query: str, params: dict[str, Any] | None = None,
) -> int:
    return await _get_client().execute_update(query, params)


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
