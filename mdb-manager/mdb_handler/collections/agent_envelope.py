# -*- coding: utf-8 -*-
"""AgentEnvelopeCollection — agent_data / agent_output 전용 동기 SDK.

모든 read/write가 자기 caller(SERVICE_NAME)로 자동 스코프된다.
cross-agent 조회가 필요하면 BlocksCollection의 raw find 사용.
"""

from __future__ import annotations

from typing import Any

from mdb_handler._paths import (
    build_envelope_delete_body,
    build_envelope_find_body,
    build_envelope_find_one_body,
    build_envelope_insert_body,
    build_envelope_update_body,
)
from mdb_handler._transport import SyncTransport


class AgentEnvelopeCollection:
    """envelope 컬렉션(agent_data / agent_output) 전용 SDK 래퍼."""

    def __init__(
        self,
        client: SyncTransport,
        collection_name: str,
        database: str = "kw_ai",
    ) -> None:
        if collection_name not in ("agent_data", "agent_output"):
            raise ValueError(
                f"AgentEnvelopeCollection supports only 'agent_data' and "
                f"'agent_output', got '{collection_name}'"
            )
        self._client = client
        self.collection_name = collection_name
        self.database = database

    @property
    def _prefix(self) -> str:
        return f"/v1/{self.database}/{self.collection_name}"

    def insert(
        self,
        *,
        type: str,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> str:
        resp = self._client.post(
            f"{self._prefix}/insert",
            json=build_envelope_insert_body(
                type_=type, metadata=metadata, data=data, ttl=ttl
            ),
        )
        return resp["inserted_id"]

    def find(
        self,
        filter: dict[str, Any] | None = None,
        *,
        projection: dict[str, Any] | None = None,
        sort: list | None = None,
        limit: int | None = None,
        skip: int | None = None,
    ) -> list[dict]:
        resp = self._client.post(
            f"{self._prefix}/find",
            json=build_envelope_find_body(
                filter,
                projection=projection,
                sort=sort,
                limit=limit,
                skip=skip,
            ),
        )
        return resp["data"]

    def find_one(
        self,
        filter: dict[str, Any] | None = None,
        *,
        projection: dict[str, Any] | None = None,
    ) -> dict | None:
        resp = self._client.post(
            f"{self._prefix}/find_one",
            json=build_envelope_find_one_body(filter, projection=projection),
        )
        return resp["data"]

    def update(
        self, filter: dict, update: dict, *, ttl: int | None = None
    ) -> int:
        resp = self._client.post(
            f"{self._prefix}/update",
            json=build_envelope_update_body(filter, update, ttl=ttl),
        )
        return resp["modified_count"]

    def update_many(
        self, filter: dict, update: dict, *, ttl: int | None = None
    ) -> int:
        resp = self._client.post(
            f"{self._prefix}/update_many",
            json=build_envelope_update_body(filter, update, ttl=ttl),
        )
        return resp["modified_count"]

    def delete(self, filter: dict) -> int:
        resp = self._client.post(
            f"{self._prefix}/delete",
            json=build_envelope_delete_body(filter),
        )
        return resp["deleted_count"]

    def delete_many(self, filter: dict) -> int:
        resp = self._client.post(
            f"{self._prefix}/delete_many",
            json=build_envelope_delete_body(filter),
        )
        return resp["deleted_count"]
