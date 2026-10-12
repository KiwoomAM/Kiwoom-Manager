"""winus_manager.aio — 비동기 인터페이스.

사용법:
    from winus_manager.aio import query_to_dataframe

    df = await query_to_dataframe("SELECT * FROM table WHERE code = :code", {"code": "A001"})

Handler 접속 URL은 운영 서버(http://192.7.9.45:8200)로 고정되어 있으며,
환경변수나 configure(base_url=...)로 변경할 수 없습니다.

조회마다 이름(client_query_id)과 마감(query_timeout_ms)을 keyword 로 붙일 수 있습니다.
뜻은 CONTEXT.md, 쓰는 법은 README.md 를 따릅니다.
"""

import logging
from typing import Any

import pandas as pd

from winus_manager._client import AsyncClient
from winus_manager._config import HANDLER_BASE_URL

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
    """SDK 클라이언트 정리."""
    global _instance
    if _instance is not None:
        await _instance.close()
        _instance = None


def configure(
    base_url: str | None = None, timeout: float = 30.0, auth_key: str | None = None,
) -> None:
    """SDK 클라이언트를 수동 설정.

    base_url 인자는 운영 정책상 무시되며 핸들러 URL은 항상 192.7.9.45:8200으로 고정됩니다.
    timeout 과 auth_key 가 적용됩니다.

    auth_key 는 핸들러의 접근 등급을 정하는 키입니다(레포 루트 CONTEXT.md 「auth_key」).
    주지 않으면 admin 키를 싣습니다(빈 문자열은 그대로 실려 401 로 거절됩니다). 기본값으로 두고,
    guest 동작을 시험할 때만 바꿉니다.

    동기 함수라 이전 인스턴스를 await close() 할 수 없습니다. 이미 인스턴스가 있을 때 재설정하려면
    먼저 `await close()` 를 호출하세요 — 살아 있는 인스턴스를 두고 재호출하면 이전 AsyncClient 풀이
    닫히지 못해 누수되므로 경고를 남깁니다.
    """
    global _instance
    if _instance is not None:
        logger.warning(
            "winus_manager.aio.configure() 재호출: 동기 함수라 이전 AsyncClient 풀을 "
            "닫을 수 없어 누수됨 — 재설정 전에 `await close()` 를 호출할 것"
        )
    _instance = AsyncClient(base_url=_resolve_base_url(), timeout=timeout, auth_key=auth_key)


# ── SELECT ──────────────────────────────────────────


async def query_to_dataframe(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> pd.DataFrame:
    return await _get_client().query_to_dataframe(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def fetch_all(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> list[tuple]:
    return await _get_client().fetch_all(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def fetch_all_dict(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> list[dict]:
    return await _get_client().fetch_all_dict(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def fetch_one(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> tuple | None:
    return await _get_client().fetch_one(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


# ── DML ────────────────────────────────────────────


async def execute_dml(
    sql: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    return await _get_client().execute_dml(
        sql, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def execute_many_dml(
    sql: str, params_list: list[dict[str, Any]], *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    return await _get_client().execute_many_dml(
        sql, params_list,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def execute_insert(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    return await _get_client().execute_insert(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


async def execute_update(
    query: str, params: dict[str, Any] | None = None, *,
    client_query_id: str | None = None, query_timeout_ms: int | None = None,
) -> int:
    return await _get_client().execute_update(
        query, params,
        client_query_id=client_query_id, query_timeout_ms=query_timeout_ms,
    )


# ── Health ─────────────────────────────────────────


async def check_database_connection() -> bool:
    return await _get_client().check_database_connection()
