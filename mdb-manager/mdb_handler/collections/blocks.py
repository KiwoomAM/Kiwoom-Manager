# -*- coding: utf-8 -*-
"""BlocksCollection -- HTTP 래퍼로 기존 시그니처를 유지하며 Service API를 호출."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from mdb_handler._client import SyncClient

logger = logging.getLogger("mdb_handler.blocks")


class BlocksCollection:
    """
    블록 컬렉션 CRUD / 집계 -- HTTP 클라이언트 래퍼.

    기존 pymongo 기반 BlocksCollection과 동일한 메서드 시그니처를 유지합니다.
    내부적으로 MDB Handler Service의 REST API를 호출합니다.
    """

    def __init__(
        self,
        client: SyncClient,
        collection_name: str = "agent_data",
        database: str = "kw_ai",
    ):
        self._client = client
        self.collection_name = collection_name
        self.database = database
        self._prefix = f"/v1/{database}/{collection_name}"

    # =========================================================================
    # Create
    # =========================================================================

    def insert_one(self, document: Dict) -> str:
        resp = self._client.post(
            f"{self._prefix}/insert_one", json={"document": document}
        )
        return resp["inserted_id"]

    def insert_many(
        self, documents: List[Dict], ordered: bool = True
    ) -> List[str]:
        if not documents:
            return []
        resp = self._client.post(
            f"{self._prefix}/insert_many",
            json={"documents": documents, "ordered": ordered},
        )
        return resp["inserted_ids"]

    # =========================================================================
    # Read
    # =========================================================================

    def get_all(self) -> List[Dict]:
        resp = self._client.post(f"{self._prefix}/find", json={"filter": {}})
        return resp["data"]

    def get_by_id(self, block_id: str) -> Optional[Dict]:
        resp = self._client.post(
            f"{self._prefix}/find_one",
            json={"filter": {"block_id": block_id}},
        )
        return resp["data"]

    def get_by_source(self, source_pdf: str) -> List[Dict]:
        resp = self._client.post(
            f"{self._prefix}/find",
            json={"filter": {"_metadata.source_pdf": source_pdf}},
        )
        return resp["data"]

    def get_sample(self, n: int = 5) -> List[Dict]:
        resp = self._client.post(
            f"{self._prefix}/find", json={"filter": {}, "limit": n}
        )
        return resp["data"]

    def find(self, filter: Dict = None, **kwargs) -> List[Dict]:
        body: dict = {"filter": filter or {}}
        for key in ("projection", "sort", "limit", "skip"):
            if key in kwargs:
                body[key] = kwargs[key]
        resp = self._client.post(f"{self._prefix}/find", json=body)
        return resp["data"]

    def find_one(self, filter: Dict = None, **kwargs) -> Optional[Dict]:
        body: dict = {"filter": filter or {}}
        if "projection" in kwargs:
            body["projection"] = kwargs["projection"]
        resp = self._client.post(f"{self._prefix}/find_one", json=body)
        return resp["data"]

    # =========================================================================
    # Update
    # =========================================================================

    def update_one(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = self._client.post(
            f"{self._prefix}/update_one",
            json={"filter": filter, "update": update, "upsert": upsert},
        )
        return resp["modified_count"]

    def update_many(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        resp = self._client.post(
            f"{self._prefix}/update_many",
            json={"filter": filter, "update": update, "upsert": upsert},
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
            f"{self._prefix}/replace_one",
            json={
                "filter": filter,
                "replacement": replacement,
                "upsert": upsert,
            },
        )
        return resp["modified_count"]

    # =========================================================================
    # Delete
    # =========================================================================

    def delete_one(self, filter: Dict) -> int:
        resp = self._client.post(
            f"{self._prefix}/delete_one", json={"filter": filter}
        )
        return resp["deleted_count"]

    def delete_many(self, filter: Dict) -> int:
        resp = self._client.post(
            f"{self._prefix}/delete_many", json={"filter": filter}
        )
        return resp["deleted_count"]

    def delete_by_id(self, block_id: str) -> int:
        return self.delete_one({"block_id": block_id})

    def delete_by_source(self, source_pdf: str) -> int:
        return self.delete_many({"_metadata.source_pdf": source_pdf})

    def drop(self) -> None:
        self._client.delete(f"{self._prefix}/drop")

    # =========================================================================
    # 메타데이터 / 통계
    # =========================================================================

    def get_source_list(self) -> List[str]:
        resp = self._client.post(
            f"{self._prefix}/distinct",
            json={"field": "_metadata.source_pdf"},
        )
        return resp["data"]

    def count(self, filter: Dict = None) -> int:
        resp = self._client.post(
            f"{self._prefix}/count", json={"filter": filter or {}}
        )
        return resp["count"]

    def count_by_source(self) -> Dict[str, int]:
        pipeline = [
            {"$group": {"_id": "$_metadata.source_pdf", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        resp = self._client.post(
            f"{self._prefix}/aggregate", json={"pipeline": pipeline}
        )
        return {doc["_id"]: doc["count"] for doc in resp["data"]}

    def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        resp = self._client.post(
            f"{self._prefix}/aggregate", json={"pipeline": pipeline}
        )
        return resp["data"]
