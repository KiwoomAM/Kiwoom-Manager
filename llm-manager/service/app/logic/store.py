"""캐시 저장/조회."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from llm_handler.models import LLMResponse, StreamChunk
from mdb_handler import MongoDBHandler

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DB = "kw_ai"

def _get_collection(collection: str, database: str | None = None):
    db_name = database or os.getenv("MONGODB_DATABASE", DEFAULT_HISTORY_DB)
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
        doc: _id, canonical_json, result, meta, created_at 등을 포함하는 문서.
        collection: 저장 대상 컬렉션명.
        database: DB명 (None이면 env → 기본값 kw_ai)
    """
    if "_id" not in doc:
        raise ValueError("cache_set: doc에 _id가 없습니다.")

    col = _get_collection(collection, database=database)
    col.replace_one({"_id": doc["_id"]}, doc, upsert=True)


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
    file_meta: list[dict] | None = None,
) -> None:
    """LLM 결과를 캐시 문서로 구성하여 저장한다.

    llm_result가 Pydantic 모델(LLMResponse 등)이면 자동으로 dict 변환 후 저장한다.
    """
    result_dict = llm_result.model_dump() if hasattr(llm_result, "model_dump") else llm_result

    now = datetime.now(UTC)
    doc: dict[str, Any] = {
        "_id": cache_key,
        "canonical_json": canonical_json,
        "result": result_dict,
        "metadata": meta,
        "created_at": now.isoformat(),
    }
    if file_meta:
        doc["file_meta"] = file_meta
    if requested_at is not None:
        doc["requested_at"] = requested_at.isoformat()
        doc["elapsed_ms"] = round((now - requested_at).total_seconds() * 1000)
    if ttl_seconds is not None:
        doc["expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
    cache_set(doc, collection, database=database)


def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
    as_stream: bool = False,
) -> tuple[LLMResponse, dict[str, Any]] | tuple[Iterator[StreamChunk], dict[str, Any]]:
    """cache_key로 MongoDB에서 캐시된 LLM 결과를 조회하여 반환한다.

    Args:
        cache_key: done_check에서 받은 SHA-256 캐시 키.
        database: DB명 (None이면 kw_ai).
        as_stream: True면 StreamChunk Iterator로 반환.

    Returns:
        (LLMResponse, meta) 또는 (Iterator[StreamChunk], meta).

    Raises:
        KeyError: cache_key에 해당하는 문서가 없을 때.
        ValueError: result 역직렬화 실패 시.
    """
    from app.logic.done_check import DEFAULT_COLLECTION

    doc = cache_get(cache_key, DEFAULT_COLLECTION, database=database)

    if doc is None:
        logger.error(
            "캐시 조회 실패: cache_key=%s 에 해당하는 문서 없음",
            cache_key[:16] + "...",
        )
        raise KeyError(f"cache_key '{cache_key[:16]}...' 에 해당하는 캐시 문서가 없습니다.")

    try:
        response = LLMResponse.model_validate(doc.get("result", {}))
    except Exception as e:
        logger.error(
            "캐시 결과 역직렬화 실패: cache_key=%s",
            cache_key[:16] + "...",
            exc_info=True,
        )
        raise ValueError(f"cache_key '{cache_key[:16]}...' 결과 역직렬화 실패") from e

    meta: dict[str, Any] = {
        "source": "cache",
        "cache_hit": True,
        "cache_key": cache_key,
        "metadata": doc.get("metadata", {}),
        "collection": DEFAULT_COLLECTION,
    }

    if as_stream:
        def _iter() -> Iterator[StreamChunk]:
            yield StreamChunk(
                text=response.text,
                finish_reason=response.finish_reason,
                usage=response.usage,
                function_calls=response.function_calls,
            )
        return _iter(), meta

    return response, meta
