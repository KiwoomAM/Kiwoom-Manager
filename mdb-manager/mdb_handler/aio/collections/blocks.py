# -*- coding: utf-8 -*-
"""AsyncBlocksCollection — BlocksCollection의 비동기 미러.

_paths.py의 빌더를 재사용해 경로/바디 로직을 동기 버전과 공유한다.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from mdb_handler._paths import (
    CollectionRoute,
    build_aggregate_body,
    build_count_body,
    build_delete_body,
    build_distinct_body,
    build_find_body,
    build_find_one_body,
    build_insert_many_body,
    build_insert_one_body,
    build_replace_body,
    build_update_body,
)
from mdb_handler._transport import AsyncTransport


class AsyncBlocksCollection:
    """블록 컬렉션 비동기 CRUD / 집계 (HTTP 클라이언트 래퍼)."""

    def __init__(
        self,
        client: AsyncTransport,
        collection_name: str = "agent_data",
        database: str = "kw_ai",
    ) -> None:
        self._client = client
        self.collection_name = collection_name
        self.database = database
        self._route = CollectionRoute(database=database, collection=collection_name)

    # Create
    async def insert_one(self, document: Dict, ttl: int | None = None) -> str:
        """문서를 삽입한다.

        Args:
            document: 저장할 문서.
            ttl: 문서 만료까지의 시간(초). None이면 만료 없음.
        """
        resp = await self._client.post(
            self._route.path("insert_one"),
            json=build_insert_one_body(document, ttl=ttl),
        )
        return resp["inserted_id"]

    async def insert_many(
        self,
        documents: List[Dict],
        ordered: bool = True,
        ttl: int | None = None,
    ) -> List[str]:
        """문서 다건을 삽입한다.

        Args:
            documents: 저장할 문서 리스트.
            ordered: 순차 처리 여부.
            ttl: 각 문서 만료까지의 시간(초). 모든 문서에 동일 적용. None이면 만료 없음.
        """
        if not documents:
            return []
        resp = await self._client.post(
            self._route.path("insert_many"),
            json=build_insert_many_body(documents, ordered=ordered, ttl=ttl),
        )
        return resp["inserted_ids"]

    # Read
    async def get_all(self) -> List[Dict]:
        resp = await self._client.post(
            self._route.path("find"), json=build_find_body()
        )
        return resp["data"]

    async def get_by_id(self, block_id: str) -> Optional[Dict]:
        resp = await self._client.post(
            self._route.path("find_one"),
            json=build_find_one_body({"block_id": block_id}),
        )
        return resp["data"]

    async def get_by_source(self, source_pdf: str) -> List[Dict]:
        resp = await self._client.post(
            self._route.path("find"),
            json=build_find_body({"_metadata.source_pdf": source_pdf}),
        )
        return resp["data"]

    async def get_sample(self, n: int = 5) -> List[Dict]:
        resp = await self._client.post(
            self._route.path("find"), json=build_find_body(limit=n)
        )
        return resp["data"]

    async def find(self, filter: Dict = None, **kwargs) -> List[Dict]:
        resp = await self._client.post(
            self._route.path("find"),
            json=build_find_body(
                filter,
                projection=kwargs.get("projection"),
                sort=kwargs.get("sort"),
                limit=kwargs.get("limit"),
                skip=kwargs.get("skip"),
            ),
        )
        return resp["data"]

    async def find_one(self, filter: Dict = None, **kwargs) -> Optional[Dict]:
        resp = await self._client.post(
            self._route.path("find_one"),
            json=build_find_one_body(filter, projection=kwargs.get("projection")),
        )
        return resp["data"]

    # Update
    async def update_one(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = await self._client.post(
            self._route.path("update_one"),
            json=build_update_body(filter, update, upsert=upsert),
        )
        return resp["modified_count"]

    async def update_many(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = await self._client.post(
            self._route.path("update_many"),
            json=build_update_body(filter, update, upsert=upsert),
        )
        return resp["modified_count"]

    async def update_by_id(
        self, block_id: str, update: Dict, upsert: bool = False
    ) -> int:
        return await self.update_one(
            {"block_id": block_id}, update, upsert=upsert
        )

    async def replace_one(
        self, filter: Dict, replacement: Dict, upsert: bool = False
    ) -> int:
        resp = await self._client.post(
            self._route.path("replace_one"),
            json=build_replace_body(filter, replacement, upsert=upsert),
        )
        return resp["modified_count"]

    # Delete
    async def delete_one(self, filter: Dict) -> int:
        resp = await self._client.post(
            self._route.path("delete_one"), json=build_delete_body(filter)
        )
        return resp["deleted_count"]

    async def delete_many(self, filter: Dict) -> int:
        resp = await self._client.post(
            self._route.path("delete_many"), json=build_delete_body(filter)
        )
        return resp["deleted_count"]

    async def delete_by_id(self, block_id: str) -> int:
        return await self.delete_one({"block_id": block_id})

    async def delete_by_source(self, source_pdf: str) -> int:
        return await self.delete_many({"_metadata.source_pdf": source_pdf})

    async def drop(self) -> None:
        await self._client.delete(self._route.path("drop"))

    # 메타데이터 / 통계
    async def get_source_list(self) -> List[str]:
        resp = await self._client.post(
            self._route.path("distinct"),
            json=build_distinct_body("_metadata.source_pdf"),
        )
        return resp["data"]

    async def count(self, filter: Dict = None) -> int:
        resp = await self._client.post(
            self._route.path("count"), json=build_count_body(filter)
        )
        return resp["count"]

    async def count_by_source(self) -> Dict[str, int]:
        pipeline = [
            {"$group": {"_id": "$_metadata.source_pdf", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        resp = await self._client.post(
            self._route.path("aggregate"), json=build_aggregate_body(pipeline)
        )
        return {doc["_id"]: doc["count"] for doc in resp["data"]}

    async def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        resp = await self._client.post(
            self._route.path("aggregate"), json=build_aggregate_body(pipeline)
        )
        return resp["data"]
