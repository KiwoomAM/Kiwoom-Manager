# -*- coding: utf-8 -*-
"""mdb-handler 접근 래퍼 — file-manager의 메타데이터 오케스트레이션 부분.

file-manager가 자기 핸들러(file-handler)는 내장 클라이언트로, 메타데이터(MongoDB)는
mdb-manager SDK(→ mdb-handler HTTP)로 접근한다. caller는 호출 시점에 with_caller로
per-call 주입한다(llm-manager 패턴). mdb-handler의 file_cache ACL(caller="file-manager")과
일치한다.

base_url/database는 import 시점이 아니라 첫 사용 시점에 env에서 읽는다(동결 방지).
"""

from __future__ import annotations

import os

from mdb_manager.aio import AsyncMongoDBManager
from mdb_manager.exceptions import (
    MDBConnectionError as SdkMDBConnectionError,
    MDBHandlerError as SdkMDBHandlerError,
)

from file_manager.exceptions import MDBConnectionError

DEFAULT_MDB_HANDLER_URL = "http://mdb-handler:8300"
DEFAULT_DATABASE = "kw_ai"
# 캐시 컬렉션은 env로 덮어쓰지 못하게 상수 고정(엉뚱한 컬렉션 사고 방지).
CACHE_COLLECTION = "file_cache"
CALLER = "file-manager"

_mdb: AsyncMongoDBManager | None = None


def _resolve_base_url() -> str:
    return os.environ.get("MDB_HANDLER_URL", DEFAULT_MDB_HANDLER_URL)


def _resolve_database() -> str:
    return os.environ.get("MONGODB_DATABASE", DEFAULT_DATABASE)


def _get_mdb() -> AsyncMongoDBManager:
    global _mdb
    if _mdb is None:
        _mdb = AsyncMongoDBManager(
            database=_resolve_database(),
            collection=CACHE_COLLECTION,
            base_url=_resolve_base_url(),
        )
    return _mdb


async def close_mdb() -> None:
    global _mdb
    if _mdb is not None:
        await _mdb.aclose()
        _mdb = None


async def find_metadata(filter: dict) -> dict | None:
    """단일 메타데이터 문서 조회. SDK 예외를 MDBConnectionError로 번역."""
    try:
        return await _get_mdb().with_caller(CALLER).find_one(filter)
    except SdkMDBConnectionError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except SdkMDBHandlerError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.status_code}): {e.message}"
        ) from e


async def insert_metadata(document: dict) -> str:
    """단일 메타데이터 문서 삽입. SDK 예외를 MDBConnectionError로 번역."""
    try:
        return await _get_mdb().with_caller(CALLER).insert_one(document)
    except SdkMDBConnectionError as e:
        raise MDBConnectionError(f"MDB Handler 연결 실패: {e}") from e
    except SdkMDBHandlerError as e:
        raise MDBConnectionError(
            f"MDB Handler 응답 오류 ({e.status_code}): {e.message}"
        ) from e
