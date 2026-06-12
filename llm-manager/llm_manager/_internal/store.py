"""캐시 저장/조회."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from mdb_manager import MongoDBManager

from llm_manager._internal.handler_client.models import LLMResponse
from llm_manager._internal.meta import require_service_name_for_save

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DB = "kw_ai"

def _get_collection(collection: str, database: str | None = None):
    db_name = database or os.getenv("MONGODB_DATABASE", DEFAULT_HISTORY_DB)
    mdb = MongoDBManager()
    # caller 는 전역 SERVICE_NAME 이 아니라 이 호출에서만 per-call 로 주입한다 —
    # agent_llm write 는 mdb-handler ACL 상 caller="llm-manager" 가 필요하고,
    # with_caller 는 싱글톤·호스트 정체성을 건드리지 않는 bound view 를 돌려준다.
    return mdb.with_caller("llm-manager").get_database(db_name, collection)


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
    file_metas: list[dict] | None = None,
) -> None:
    """LLM 결과를 캐시 문서로 구성하여 저장한다.

    llm_result가 Pydantic 모델(LLMResponse 등)이면 자동으로 dict 변환 후 저장한다.

    file_metas: FileInput의 meta 필드 dump 리스트 (data 제외). 저장 스키마
    호환을 위해 문서엔 기존 키 이름 "file_meta"로 저장한다.
    """
    require_service_name_for_save(meta)

    result_dict = llm_result.model_dump() if hasattr(llm_result, "model_dump") else llm_result

    now = datetime.now(UTC)
    doc: dict[str, Any] = {
        "_id": cache_key,
        "canonical_json": canonical_json,
        "result": result_dict,
        "metadata": meta,
        "created_at": now.isoformat(),
    }
    if file_metas:
        doc["file_meta"] = file_metas
    if requested_at is not None:
        doc["requested_at"] = requested_at.isoformat()
    if ttl_seconds is not None:
        doc["expires_at"] = (now + timedelta(seconds=ttl_seconds)).isoformat()
    cache_set(doc, collection, database=database)


def fetch_by_cache_key(
    cache_key: str,
    *,
    database: str | None = None,
) -> tuple[LLMResponse, dict[str, Any]]:
    """cache_key로 MongoDB에서 캐시된 LLM 결과를 조회하여 반환한다.

    외부 사용자 요청으로 추가된 공개 API — done_check으로 얻은 cache_key를
    들고 결과만 직접 꺼내고 싶을 때 사용한다. llm_request_async와 별개 경로.

    Args:
        cache_key: done_check에서 받은 SHA-256 캐시 키.
        database: DB명 (None이면 kw_ai).

    Returns:
        (LLMResponse, meta)

    Raises:
        KeyError: cache_key에 해당하는 문서가 없을 때.
        ValueError: result 역직렬화 실패 시.
    """
    from llm_manager._internal.done_check import DEFAULT_COLLECTION

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
    file_metas = doc.get("file_meta")
    if file_metas is not None:
        meta["file_meta"] = file_metas

    return response, meta
