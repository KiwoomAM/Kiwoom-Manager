# -*- coding: utf-8 -*-
"""BlocksCollection -- HTTP 래퍼로 기존 시그니처를 유지하며 Service API를 호출."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from mdb_handler._paths import (
    CollectionRoute,
    build_aggregate_body,
    build_count_body,
    build_delete_body,
    build_distinct_body,
    build_envelope_insert_body,
    build_find_body,
    build_find_one_body,
    build_insert_many_body,
    build_insert_one_body,
    build_replace_body,
    build_update_body,
)
from mdb_handler._transport import SyncTransport

logger = logging.getLogger("mdb_handler.blocks")


class BlocksCollection:
    """블록 컬렉션 CRUD / 집계 -- HTTP 클라이언트 래퍼."""

    def __init__(
        self,
        client: SyncTransport,
        collection_name: str = "agent_data",
        database: str = "kw_ai",
    ):
        self._client = client
        self.collection_name = collection_name
        self.database = database
        self._route = CollectionRoute(database=database, collection=collection_name)

    @property
    def _prefix(self) -> str:
        return self._route.prefix

    # Create
    def insert_one(self, document: Dict, ttl: int | None = None) -> str:
        """문서를 삽입한다.

        Args:
            document: 저장할 문서.
            ttl: 문서 만료까지의 시간(초). None이면 만료 없음.
        """
        resp = self._client.post(
            self._route.path("insert_one"),
            json=build_insert_one_body(document, ttl=ttl),
        )
        return resp["inserted_id"]

    def insert_many(
        self,
        documents: List[Dict],
        ordered: bool = True,
        ttl: int | None = None,
    ) -> List[str]:
        """문서 다건을 삽입한다.

        Args:
            documents: 저장할 문서 리스트.
            ordered: 순차 처리 여부 (실패 시 중단).
            ttl: 각 문서 만료까지의 시간(초). 모든 문서에 동일하게 적용. None이면 만료 없음.
        """
        if not documents:
            return []
        resp = self._client.post(
            self._route.path("insert_many"),
            json=build_insert_many_body(documents, ordered=ordered, ttl=ttl),
        )
        return resp["inserted_ids"]

    # Read
    def get_all(self) -> List[Dict]:
        resp = self._client.post(
            self._route.path("find"), json=build_find_body()
        )
        return resp["data"]

    def get_by_id(self, block_id: str) -> Optional[Dict]:
        resp = self._client.post(
            self._route.path("find_one"),
            json=build_find_one_body({"block_id": block_id}),
        )
        return resp["data"]

    def get_by_source(self, source_pdf: str) -> List[Dict]:
        resp = self._client.post(
            self._route.path("find"),
            json=build_find_body({"_metadata.source_pdf": source_pdf}),
        )
        return resp["data"]

    def get_sample(self, n: int = 5) -> List[Dict]:
        resp = self._client.post(
            self._route.path("find"), json=build_find_body(limit=n)
        )
        return resp["data"]

    def find(self, filter: Dict = None, **kwargs) -> List[Dict]:
        resp = self._client.post(
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

    def find_one(self, filter: Dict = None, **kwargs) -> Optional[Dict]:
        resp = self._client.post(
            self._route.path("find_one"),
            json=build_find_one_body(filter, projection=kwargs.get("projection")),
        )
        return resp["data"]

    # Update
    def update_one(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = self._client.post(
            self._route.path("update_one"),
            json=build_update_body(filter, update, upsert=upsert),
        )
        return resp["modified_count"]

    def update_many(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = self._client.post(
            self._route.path("update_many"),
            json=build_update_body(filter, update, upsert=upsert),
        )
        return resp["modified_count"]

    def update_by_id(
        self, block_id: str, update: Dict, upsert: bool = False
    ) -> int:
        return self.update_one({"block_id": block_id}, update, upsert=upsert)

    def replace_one(
        self, filter: Dict, replacement: Dict, upsert: bool = False
    ) -> int:
        resp = self._client.post(
            self._route.path("replace_one"),
            json=build_replace_body(filter, replacement, upsert=upsert),
        )
        return resp["modified_count"]

    # Delete
    def delete_one(self, filter: Dict) -> int:
        resp = self._client.post(
            self._route.path("delete_one"), json=build_delete_body(filter)
        )
        return resp["deleted_count"]

    def delete_many(self, filter: Dict) -> int:
        resp = self._client.post(
            self._route.path("delete_many"), json=build_delete_body(filter)
        )
        return resp["deleted_count"]

    def delete_by_id(self, block_id: str) -> int:
        return self.delete_one({"block_id": block_id})

    def delete_by_source(self, source_pdf: str) -> int:
        return self.delete_many({"_metadata.source_pdf": source_pdf})

    def drop(self) -> None:
        self._client.delete(self._route.path("drop"))

    # 메타/집계
    def get_source_list(self) -> List[str]:
        resp = self._client.post(
            self._route.path("distinct"),
            json=build_distinct_body("_metadata.source_pdf"),
        )
        return resp["data"]

    def count(self, filter: Dict = None) -> int:
        resp = self._client.post(
            self._route.path("count"), json=build_count_body(filter)
        )
        return resp["count"]

    def count_by_source(self) -> Dict[str, int]:
        pipeline = [
            {"$group": {"_id": "$_metadata.source_pdf", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        resp = self._client.post(
            self._route.path("aggregate"), json=build_aggregate_body(pipeline)
        )
        return {doc["_id"]: doc["count"] for doc in resp["data"]}

    def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        resp = self._client.post(
            self._route.path("aggregate"), json=build_aggregate_body(pipeline)
        )
        return resp["data"]

    # Envelope insert (agent_data / agent_output 전용)
    def insert_agent_data(
        self,
        *,
        type: str,
        metadata: Dict | None = None,
        data: Dict | None = None,
        ttl: int | None = None,
    ) -> str:
        """envelope 형태로 agent_data 컬렉션에 삽입한다.

        Service가 metadata.depart(=SERVICE_NAME) / metadata.type /
        created_at을 자동 채우므로 호출자는 비즈니스 페이로드만 제공한다.
        현재 BlocksCollection의 database 속성을 그대로 사용한다.
        """
        resp = self._client.post(
            f"/v1/{self.database}/agent_data/insert",
            json=build_envelope_insert_body(
                type_=type, metadata=metadata, data=data, ttl=ttl
            ),
        )
        return resp["inserted_id"]

    def insert_agent_output(
        self,
        *,
        type: str,
        metadata: Dict | None = None,
        data: Dict | None = None,
        ttl: int | None = None,
    ) -> str:
        """envelope 형태로 agent_output 컬렉션에 삽입한다."""
        resp = self._client.post(
            f"/v1/{self.database}/agent_output/insert",
            json=build_envelope_insert_body(
                type_=type, metadata=metadata, data=data, ttl=ttl
            ),
        )
        return resp["inserted_id"]
