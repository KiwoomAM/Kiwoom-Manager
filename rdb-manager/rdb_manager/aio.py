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

조회·DML 은 keyword 인자 database 로 접속할 database 를 고를 수 있습니다. 고르지 않으면
핸들러의 기본 database 에 붙습니다(README 「database 고르기」).

Handler 접속 URL은 운영 서버(http://192.7.9.45:8700)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다.
"""

import logging
from typing import Any

import pandas as pd

from rdb_manager._client import AsyncClient
from rdb_manager._config import HANDLER_BASE_URL

logger = logging.getLogger(__name__)

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


def configure(
    base_url: str | None = None, timeout: float = 30.0, auth_key: str | None = None,
) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8700으로 고정됩니다.
    timeout 과 auth_key 가 적용됩니다.

    auth_key 는 핸들러의 접근 등급을 정하는 키입니다(레포 루트 CONTEXT.md 「auth_key」).
    주지 않으면 admin 키를 싣습니다(빈 문자열은 그대로 실려 401 로 거절됩니다). 기본값으로 두고,
    guest 동작을 시험할 때만 바꿉니다.

    주의: 동기 함수이므로 이전 인스턴스를 await close()할 수 없다. 이미
    인스턴스가 생성된 뒤 재설정하려면 먼저 `await close()`를 호출해 기존
    연결 풀을 정리한 다음 configure()를 호출하라 (풀 누수 방지).
    살아있는 인스턴스를 두고 재호출하면 이전 AsyncClient 풀이 닫히지 못해
    누수되므로 런타임 경고를 남긴다.
    """
    global _instance
    if _instance is not None:
        logger.warning(
            "rdb_manager.aio.configure() 재호출: 동기 함수라 이전 AsyncClient 풀을 "
            "닫을 수 없어 누수됩니다. 재설정 전에 `await close()`를 호출하세요."
        )
    _instance = AsyncClient(base_url=_resolve_base_url(), timeout=timeout, auth_key=auth_key)


# ── SELECT ──────────────────────────────────────────


async def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> pd.DataFrame:
    return await _get_client().query_to_dataframe(query, params, database=database)


async def fetch_all(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> list[tuple]:
    return await _get_client().fetch_all(query, params, database=database)


async def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> list[dict]:
    return await _get_client().fetch_all_dict(query, params, database=database)


async def fetch_one(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> tuple | None:
    return await _get_client().fetch_one(query, params, database=database)


# ── DML ────────────────────────────────────────────


async def execute_dml(
    sql: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> int:
    return await _get_client().execute_dml(sql, params, database=database)


async def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]], *, database: str | None = None,
) -> int:
    return await _get_client().execute_many_dml(sql, params_list, database=database)


async def execute_insert(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> int:
    return await _get_client().execute_insert(query, params, database=database)


async def execute_update(
    query: str, params: dict[str, Any] | None = None, *, database: str | None = None,
) -> int:
    return await _get_client().execute_update(query, params, database=database)


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
