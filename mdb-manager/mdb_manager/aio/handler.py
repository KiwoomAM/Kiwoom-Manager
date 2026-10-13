# -*- coding: utf-8 -*-
"""AsyncMongoDBManager — 비동기 진입점.

Singleton을 의도적으로 피한다. async 환경(FastAPI lifespan, LangGraph)에서는
명시적 인스턴스 + 명시적 aclose()가 자원 관리에 훨씬 안전하다.

사용 예시:
    async with AsyncMongoDBManager(database="kw_rag", collection="kw_gp") as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)

    # FastAPI lifespan에서:
    mdb = AsyncMongoDBManager()
    app.state.mdb = mdb
    try:
        yield
    finally:
        await mdb.aclose()
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from mdb_manager._async_client import AsyncClient, _AsyncCallerBoundClient
from mdb_manager.aio.collections.agent_envelope import (
    AsyncAgentEnvelopeCollection,
)
from mdb_manager.aio.collections.blocks import AsyncBlocksCollection
from mdb_manager.exceptions import MDBConfigError

logger = logging.getLogger(__name__)


_ENVELOPE_CALLER_REQUIRED = (
    "envelope 컬렉션(agent_data/agent_output) 접근에는 caller 식별이 필요합니다. "
    "SERVICE_NAME 환경변수를 설정하거나 with_caller(...)로 caller를 지정하세요."
)


def _require_caller(client) -> None:
    """envelope 진입 전 caller(env SERVICE_NAME 또는 bound caller) 존재를 강제."""
    if not client.caller_identity:
        raise MDBConfigError(_ENVELOPE_CALLER_REQUIRED)


class _BoundAsyncManager:
    """특정 caller로 묶인 AsyncMongoDBManager view (불변).

    CRUD는 caller-bound 기본 컬렉션으로 __getattr__ 위임, 컬렉션 getter는
    같은 caller로 bound된 컬렉션을 반환한다. 부모 핸들러의 httpx 클라이언트를
    공유하며 lifecycle은 부모가 관리한다. 싱글톤/공유 상태를 변경하지 않는다.
    """

    def __init__(
        self, base_client: AsyncClient, database: str, collection: str, caller: str
    ) -> None:
        self._client = _AsyncCallerBoundClient(base_client, caller)
        self.database = database
        self.collection_name = collection
        self._default = AsyncBlocksCollection(self._client, collection, database)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._default, name)

    def get_collection(self, collection_name: str) -> AsyncBlocksCollection:
        return AsyncBlocksCollection(self._client, collection_name, self.database)

    def get_database(
        self, database: str, collection: str | None = None
    ) -> AsyncBlocksCollection:
        return AsyncBlocksCollection(
            self._client, collection or "agent_data", database
        )

    def get_agent_data(
        self, database: str | None = None
    ) -> AsyncAgentEnvelopeCollection:
        _require_caller(self._client)
        return AsyncAgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_data",
            database=database or self.database,
        )

    def get_agent_output(
        self, database: str | None = None
    ) -> AsyncAgentEnvelopeCollection:
        _require_caller(self._client)
        return AsyncAgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_output",
            database=database or self.database,
        )


class AsyncMongoDBManager:
    """MongoDB 통합 비동기 핸들러 (HTTP 클라이언트 래퍼)."""

    def __init__(
        self,
        database: str | None = None,
        collection: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        auth_key: str | None = None,
    ) -> None:
        """기본 database·collection 과 접근 키로 클라이언트를 만든다.

        Args:
            database: 기본 database. 없으면 kw_ai.
            collection: 기본 collection. 없으면 agent_data.
            base_url: 핸들러 URL. 없으면 MDB_HANDLER_URL 또는 운영 서버.
            timeout: 요청 타임아웃(초). 없으면 30.
            auth_key: 핸들러의 접근 등급을 정하는 키(레포 루트 CONTEXT.md 「auth_key」). 주지 않으면
                admin 키를 싣는다(빈 문자열은 그대로 실려 401 로 거절된다). 기본값으로 두고, guest 동작을
                시험할 때만 바꾼다.
        """
        self.database = database or "kw_ai"
        self.collection_name = collection or "agent_data"
        self._client = AsyncClient(
            base_url=base_url,
            timeout=timeout if timeout is not None else 30.0,
            auth_key=auth_key,
        )
        self._default = AsyncBlocksCollection(
            self._client, self.collection_name, self.database
        )
        self._closed = False
        logger.info(
            "AsyncMongoDBManager 초기화 (database=%s, collection=%s)",
            self.database,
            self.collection_name,
        )

    # lifecycle
    async def aclose(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        self._closed = True
        logger.info("AsyncMongoDBManager 연결 종료")

    async def __aenter__(self) -> "AsyncMongoDBManager":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "AsyncMongoDBManager is closed. 새 인스턴스를 만들거나 aclose 호출 전에 사용하세요."
            )

    # Create
    async def insert_one(self, document: Dict, ttl: int | None = None) -> str:
        self._ensure_open()
        return await self._default.insert_one(document, ttl=ttl)

    async def insert_many(
        self,
        documents: List[Dict],
        ordered: bool = True,
        ttl: int | None = None,
    ) -> List[str]:
        self._ensure_open()
        return await self._default.insert_many(documents, ordered=ordered, ttl=ttl)

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
    async def update_one(self, filter: Dict, update: Dict, upsert: bool = False) -> int:
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
        return await self._default.replace_one(filter, replacement, upsert=upsert)

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
        resp = await self._client.get("/v1/admin/collections", params={"database": db})
        return resp["collections"]

    # 컬렉션/DB 전환
    def get_collection(self, collection_name: str) -> AsyncBlocksCollection:
        self._ensure_open()
        return AsyncBlocksCollection(self._client, collection_name, self.database)

    def get_database(
        self, database: str, collection: str | None = None
    ) -> AsyncBlocksCollection:
        self._ensure_open()
        col = collection or "agent_data"
        return AsyncBlocksCollection(self._client, col, database)

    def get_agent_data(
        self, database: str | None = None
    ) -> AsyncAgentEnvelopeCollection:
        """agent_data envelope 컬렉션 진입점 (비동기).

        모든 read/write가 SERVICE_NAME으로 자동 스코프됨.
        caller(env SERVICE_NAME 또는 with_caller) 부재 시 MDBConfigError.
        """
        self._ensure_open()
        _require_caller(self._client)
        return AsyncAgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_data",
            database=database or self.database,
        )

    def get_agent_output(
        self, database: str | None = None
    ) -> AsyncAgentEnvelopeCollection:
        """agent_output envelope 컬렉션 진입점 (비동기).

        caller(env SERVICE_NAME 또는 with_caller) 부재 시 MDBConfigError.
        """
        self._ensure_open()
        _require_caller(self._client)
        return AsyncAgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_output",
            database=database or self.database,
        )

    def with_caller(self, caller: str) -> "_BoundAsyncManager":
        """주어진 caller로 묶인 view를 반환한다 (per-call/bound override).

        싱글톤이 아니지만 부모의 공유 클라이언트를 사용하며, 정체성은
        per-request로만 흐르므로 부모 기본 호출과 격리된다.
        """
        self._ensure_open()
        return _BoundAsyncManager(
            self._client, self.database, self.collection_name, caller
        )

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return (
            f"<AsyncMongoDBManager database={self.database} "
            f"collection={self.collection_name} status={status}>"
        )
