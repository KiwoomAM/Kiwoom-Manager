"""캐시 저장/조회."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from mdb_handler import MongoDBHandler

DEFAULT_HISTORY_DB = "kw_ai"
ENV_HISTORY_DB_KEY = "MONGODB_DATABASE"


def _get_collection(collection: str, database: str | None = None):
    db_name = database or os.getenv(ENV_HISTORY_DB_KEY, DEFAULT_HISTORY_DB)
    mdb = MongoDBHandler()
    return mdb.get_database(db_name, collection)


def cache_get(
    cache_key: str,
    collection: str,
    database: str | None = None,
) -> dict[str, Any] | None:
    """캐시에서 cache_key로 문서를 조회한다.

    Args:
        cache_key: 해시 기반 캐시 키(SHA-256 hex digest).
        collection: 조회 대상 컬렉션명.
        database: DB명 (None이면 env → 기본값 kw_ai)

    Returns:
        저장된 문서 dict, 없으면 None.
    """
    col = _get_collection(collection, database=database)
    return col.find_one({"_id": cache_key})


def cache_set(
    doc: dict,
    collection: str,
    database: str | None = None,
) -> None:
    """doc을 지정된 컬렉션에 upsert 저장한다.

    Args:
        doc: cache_key, canonical_json, result, meta, created_at 등을 포함하는 문서.
        collection: 저장 대상 컬렉션명.
        database: DB명 (None이면 env → 기본값 kw_ai)
    """
    cache_key = doc.get("_id") or doc.get("cache_key")
    if not cache_key:
        raise ValueError("cache_set: doc에 cache_key/_id가 없습니다.")
    doc["_id"] = cache_key

    col = _get_collection(collection, database=database)
    col.replace_one({"_id": cache_key}, doc, upsert=True)


def save_result(
    cache_key: str,
    canonical_json: str,
    llm_result: Any,
    meta: dict,
    collection: str,
    *,
    database: str | None = None,
    ttl_seconds: int | None = None,
    requested_at: datetime | None = None,
) -> None:
    """LLM 결과를 캐시 문서로 구성하여 저장한다.

    llm_result가 Pydantic 모델(LLMResponse 등)이면 자동으로 dict 변환 후 저장한다.
    """
    result_dict = llm_result.model_dump() if hasattr(llm_result, "model_dump") else llm_result

    now = datetime.now(UTC)
    doc: dict[str, Any] = {
        "cache_key": cache_key,
        "canonical_json": canonical_json,
        "result": result_dict,
        "meta": meta,
        "created_at": now.isoformat(),
    }
    if requested_at is not None:
        doc["requested_at"] = requested_at.isoformat()
        doc["elapsed_ms"] = round((now - requested_at).total_seconds() * 1000)
    if ttl_seconds is not None:
        doc["expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
    cache_set(doc, collection, database=database)
