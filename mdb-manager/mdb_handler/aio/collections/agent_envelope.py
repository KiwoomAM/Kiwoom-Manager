# -*- coding: utf-8 -*-
"""AsyncAgentEnvelopeCollection — envelope 컬렉션 전용 비동기 SDK."""

from __future__ import annotations

from typing import Any

from mdb_handler._paths import (
    build_envelope_delete_body,
    build_envelope_find_body,
    build_envelope_find_one_body,
    build_envelope_insert_body,
    build_envelope_update_body,
)
from mdb_handler._transport import AsyncTransport


class AsyncAgentEnvelopeCollection:
    """envelope 컬렉션(agent_data / agent_output) 전용 비동기 SDK 래퍼."""

    def __init__(
        self,
        client: AsyncTransport,
        collection_name: str,
        database: str = "kw_ai",
    ) -> None:
        if collection_name not in ("agent_data", "agent_output"):
            raise ValueError(
                f"AsyncAgentEnvelopeCollection supports only 'agent_data' and "
                f"'agent_output', got '{collection_name}'"
            )
        self._client = client
        self.collection_name = collection_name
        self.database = database

    @property
    def _prefix(self) -> str:
        return f"/v1/{self.database}/{self.collection_name}"

    async def insert(
        self,
        *,
        type: str,
        metadata: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> str:
        resp = await self._client.post(
            f"{self._prefix}/insert",
            json=build_envelope_insert_body(
                type_=type, metadata=metadata, data=data, ttl=ttl
            ),
        )
        return resp["inserted_id"]

    async def find(
        self,
        filter: dict[str, Any] | None = None,
        *,
        projection: dict[str, Any] | None = None,
        sort: list | None = None,
        limit: int | None = None,
        skip: int | None = None,
    ) -> list[dict]:
        resp = await self._client.post(
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

    async def find_one(
        self,
        filter: dict[str, Any] | None = None,
        *,
        projection: dict[str, Any] | None = None,
    ) -> dict | None:
        resp = await self._client.post(
            f"{self._prefix}/find_one",
            json=build_envelope_find_one_body(filter, projection=projection),
        )
        return resp["data"]

    async def update(
        self, filter: dict, update: dict, *, ttl: int | None = None
    ) -> int:
        resp = await self._client.post(
            f"{self._prefix}/update",
            json=build_envelope_update_body(filter, update, ttl=ttl),
        )
        return resp["modified_count"]

    async def update_many(
        self, filter: dict, update: dict, *, ttl: int | None = None
    ) -> int:
        resp = await self._client.post(
            f"{self._prefix}/update_many",
            json=build_envelope_update_body(filter, update, ttl=ttl),
        )
        return resp["modified_count"]

    async def delete(self, filter: dict) -> int:
        resp = await self._client.post(
            f"{self._prefix}/delete",
            json=build_envelope_delete_body(filter),
        )
        return resp["deleted_count"]

    async def delete_many(self, filter: dict) -> int:
        resp = await self._client.post(
            f"{self._prefix}/delete_many",
            json=build_envelope_delete_body(filter),
        )
        return resp["deleted_count"]
