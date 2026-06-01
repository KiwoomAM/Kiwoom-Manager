"""REST 경로/요청 바디 빌더 — sync/async 공용.

CRUD 로직 중복을 방지하기 위한 순수 함수 모듈.
전송 계층(sync/async)과 무관하게 동일한 경로와 바디를 생성한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CollectionRoute:
    """단일 컬렉션에 대한 REST 경로 네임스페이스."""

    database: str
    collection: str

    @property
    def prefix(self) -> str:
        return f"/v1/{self.database}/{self.collection}"

    def path(self, action: str) -> str:
        return f"{self.prefix}/{action}"


def build_find_body(
    filter: dict | None = None,
    *,
    projection: dict | None = None,
    sort: list | None = None,
    limit: int | None = None,
    skip: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"filter": filter or {}}
    if projection is not None:
        body["projection"] = projection
    if sort is not None:
        body["sort"] = sort
    if limit is not None:
        body["limit"] = limit
    if skip is not None:
        body["skip"] = skip
    return body


def build_find_one_body(
    filter: dict | None = None,
    *,
    projection: dict | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"filter": filter or {}}
    if projection is not None:
        body["projection"] = projection
    return body


def build_insert_one_body(document: dict, *, ttl: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"document": document}
    if ttl is not None:
        body["ttl"] = ttl
    return body


def build_insert_many_body(
    documents: list[dict],
    *,
    ordered: bool = True,
    ttl: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"documents": documents, "ordered": ordered}
    if ttl is not None:
        body["ttl"] = ttl
    return body


def build_update_body(
    filter: dict, update: dict, *, upsert: bool = False
) -> dict[str, Any]:
    return {"filter": filter, "update": update, "upsert": upsert}


def build_replace_body(
    filter: dict, replacement: dict, *, upsert: bool = False
) -> dict[str, Any]:
    return {"filter": filter, "replacement": replacement, "upsert": upsert}


def build_delete_body(filter: dict) -> dict[str, Any]:
    return {"filter": filter}


def build_distinct_body(field: str) -> dict[str, Any]:
    return {"field": field}


def build_count_body(filter: dict | None = None) -> dict[str, Any]:
    return {"filter": filter or {}}


def build_aggregate_body(pipeline: list[dict]) -> dict[str, Any]:
    return {"pipeline": pipeline}


def build_envelope_insert_body(
    *,
    type_: str,
    metadata: dict | None = None,
    data: dict | None = None,
    ttl: int | None = None,
) -> dict[str, Any]:
    """agent_data / agent_output envelope insert 요청 바디 빌더."""
    body: dict[str, Any] = {
        "type": type_,
        "metadata": metadata or {},
        "data": data or {},
    }
    if ttl is not None:
        body["ttl"] = ttl
    return body


def build_envelope_find_body(
    filter: dict | None = None,
    *,
    projection: dict | None = None,
    sort: list | None = None,
    limit: int | None = None,
    skip: int | None = None,
) -> dict[str, Any]:
    """envelope find 요청 바디 빌더 (caller scope는 Service가 강제)."""
    body: dict[str, Any] = {"filter": filter or {}}
    if projection is not None:
        body["projection"] = projection
    if sort is not None:
        body["sort"] = sort
    if limit is not None:
        body["limit"] = limit
    if skip is not None:
        body["skip"] = skip
    return body


def build_envelope_find_one_body(
    filter: dict | None = None, *, projection: dict | None = None
) -> dict[str, Any]:
    """envelope find_one 요청 바디 빌더."""
    body: dict[str, Any] = {"filter": filter or {}}
    if projection is not None:
        body["projection"] = projection
    return body


def build_envelope_update_body(
    filter: dict,
    update: dict,
    *,
    ttl: int | None = None,
    upsert: bool = False,
    type_: str | None = None,
) -> dict[str, Any]:
    """envelope update 요청 바디 빌더. update는 MongoDB operator만 허용.

    upsert=True 일 때 type_은 필수 (Service가 422). type_은 builtin shadow
    회피용 trailing underscore이며 JSON 키는 "type"으로 직렬화한다.
    """
    body: dict[str, Any] = {"filter": filter, "update": update}
    if upsert:
        body["upsert"] = True
    if type_ is not None:
        body["type"] = type_
    if ttl is not None:
        body["ttl"] = ttl
    return body


def build_envelope_delete_body(filter: dict) -> dict[str, Any]:
    """envelope delete 요청 바디 빌더."""
    return {"filter": filter}
