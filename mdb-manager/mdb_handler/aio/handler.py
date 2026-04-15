# -*- coding: utf-8 -*-
"""AsyncMongoDBHandler — 비동기 진입점.

Singleton을 의도적으로 피한다. async 환경(FastAPI lifespan, LangGraph)에서는
명시적 인스턴스 + 명시적 aclose()가 자원 관리에 훨씬 안전하다.

사용 예시:
    async with AsyncMongoDBHandler(database="kw_rag", collection="kw_gp") as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)

    # FastAPI lifespan에서:
    mdb = AsyncMongoDBHandler()
    app.state.mdb = mdb
    try:
        yield
    finally:
        await mdb.aclose()
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from mdb_handler._async_client import AsyncClient
from mdb_handler.aio.collections.blocks import AsyncBlocksCollection

logger = logging.getLogger(__name__)


class AsyncMongoDBHandler:
    """MongoDB 통합 비동기 핸들러 (HTTP 클라이언트 래퍼)."""

    def __init__(
        self,
        database: str | None = None,
        collection: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.database = database or "kw_ai"
        self.collection_name = collection or "agent_data"
        self._client = AsyncClient(
            base_url=base_url,
            timeout=timeout if timeout is not None else 30.0,
        )
        self._default = AsyncBlocksCollection(
            self._client, self.collection_name, self.database
        )
        self._closed = False
        logger.info(
            "AsyncMongoDBHandler 초기화 (database=%s, collection=%s)",
            self.database,
            self.collection_name,
        )

    # lifecycle
    async def aclose(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True
        logger.info("AsyncMongoDBHandler 연결 종료")

    async def __aenter__(self) -> "AsyncMongoDBHandler":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "AsyncMongoDBHandler is closed. 새 인스턴스를 만들거나 aclose 호출 전에 사용하세요."
            )

    # Create
    async def insert_one(self, document: Dict) -> str:
        self._ensure_open()
        return await self._default.insert_one(document)

    async def insert_many(
        self, documents: List[Dict], ordered: bool = True
    ) -> List[str]:
        self._ensure_open()
        return await self._default.insert_many(documents, ordered=ordered)

    # Read
    async def get_all(self) -> List[Dict]:
        self._ensure_open()
        return await self._default.get_all()

    async def get_by_id(self, block_id: str) -> Optional[Dict]:
        self._ensure_open()
        return await self._default.get_by_id(block_id)

    async def get_by_source(self, source_pdf: str) -> List[Dict]:
        self._ensure_open()
        return await self._default.get_by_source(source_pdf)

    async def get_sample(self, n: int = 5) -> List[Dict]:
        self._ensure_open()
        return await self._default.get_sample(n)

    async def find(self, filter: Dict = None, **kwargs) -> List[Dict]:
        self._ensure_open()
        return await self._default.find(filter, **kwargs)

    async def find_one(self, filter: Dict = None, **kwargs) -> Optional[Dict]:
        self._ensure_open()
        return await self._default.find_one(filter, **kwargs)

    # Update
    async def update_one(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        self._ensure_open()
        return await self._default.update_one(filter, update, upsert=upsert)

    async def update_many(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        self._ensure_open()
        return await self._default.update_many(filter, update, upsert=upsert)

    async def update_by_id(
        self, block_id: str, update: Dict, upsert: bool = False
    ) -> int:
        self._ensure_open()
        return await self._default.update_by_id(block_id, update, upsert=upsert)

    async def replace_one(
        self, filter: Dict, replacement: Dict, upsert: bool = False
    ) -> int:
        self._ensure_open()
        return await self._default.replace_one(
            filter, replacement, upsert=upsert
        )

    # Delete
    async def delete_one(self, filter: Dict) -> int:
        self._ensure_open()
        return await self._default.delete_one(filter)

    async def delete_many(self, filter: Dict) -> int:
        self._ensure_open()
        return await self._default.delete_many(filter)

    async def delete_by_id(self, block_id: str) -> int:
        self._ensure_open()
        return await self._default.delete_by_id(block_id)

    async def delete_by_source(self, source_pdf: str) -> int:
        self._ensure_open()
        return await self._default.delete_by_source(source_pdf)

    async def drop(self) -> None:
        self._ensure_open()
        await self._default.drop()

    # 메타/집계
    async def get_source_list(self) -> List[str]:
        self._ensure_open()
        return await self._default.get_source_list()

    async def count(self, filter: Dict = None) -> int:
        self._ensure_open()
        return await self._default.count(filter)

    async def count_by_source(self) -> Dict[str, int]:
        self._ensure_open()
        return await self._default.count_by_source()

    async def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        self._ensure_open()
        return await self._default.aggregate(pipeline)

    # admin
    async def health_check(self) -> Dict:
        self._ensure_open()
        return await self._client.get("/v1/admin/health")

    async def list_collections(self, database: str | None = None) -> List[str]:
        self._ensure_open()
        db = database or self.database
        resp = await self._client.get(
            "/v1/admin/collections", params={"database": db}
        )
        return resp["collections"]

    # 컬렉션/DB 전환
    def get_collection(self, collection_name: str) -> AsyncBlocksCollection:
        self._ensure_open()
        return AsyncBlocksCollection(
            self._client, collection_name, self.database
        )

    def get_database(
        self, database: str, collection: str | None = None
    ) -> AsyncBlocksCollection:
        self._ensure_open()
        col = collection or "agent_data"
        return AsyncBlocksCollection(self._client, col, database)

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return (
            f"<AsyncMongoDBHandler database={self.database} "
            f"collection={self.collection_name} status={status}>"
        )
