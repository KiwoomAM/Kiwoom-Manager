# -*- coding: utf-8 -*-
"""
MongoDBHandler - HTTP 클라이언트 래퍼 (Singleton)

기존 pymongo 기반 MongoDBHandler와 동일한 인터페이스를 제공합니다.
내부적으로 MDB Handler Service에 HTTP 요청을 보냅니다.

사용 예시:
    from mdb_manager import MongoDBHandler

    mdb = MongoDBHandler()

    mdb.get_all()
    mdb.insert_one({"block_id": "p1_b1", ...})
    mdb.find({"_metadata.type": "table"})

    # 같은 database, 다른 collection
    reports = mdb.get_collection("overseas_reports")
    reports.get_all()

    # 다른 database
    rag = mdb.get_database("kw_rag", "kw_gp")
    rag.get_all()

    mdb.health_check()
    mdb.close()
"""

import logging
import warnings
from typing import Dict, List, Optional

from mdb_manager._client import SyncClient
from mdb_manager.collections.agent_envelope import AgentEnvelopeCollection
from mdb_manager.collections.blocks import BlocksCollection

logger = logging.getLogger(__name__)


class MongoDBHandler:
    """
    MongoDB 통합 핸들러 (Singleton) -- HTTP 클라이언트 래퍼.

    MDB Handler Service를 통해 MongoDB에 접근합니다.
    database와 collection을 자유롭게 선택할 수 있습니다.
    """

    _instance: Optional["MongoDBHandler"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        uri: str = None,
        database: str = None,
        collection: str = None,
    ):
        if self._initialized:
            return

        if uri is not None:
            warnings.warn(
                "uri 파라미터는 무시됩니다. Service 측 설정을 사용합니다.",
                DeprecationWarning,
                stacklevel=2,
            )

        self.database = database or "kw_ai"
        self.collection_name = collection or "agent_data"
        self._client = SyncClient()
        self._default_collection = BlocksCollection(
            self._client, self.collection_name, self.database
        )

        self._initialized = True
        logger.info(
            "MongoDBHandler 초기화 완료 (database=%s, collection=%s)",
            self.database,
            self.collection_name,
        )

    # =====================================================================
    # Create
    # =====================================================================

    def insert_one(self, document: Dict, ttl: int | None = None) -> str:
        return self._default_collection.insert_one(document, ttl=ttl)

    def insert_many(
        self,
        documents: List[Dict],
        ordered: bool = True,
        ttl: int | None = None,
    ) -> List[str]:
        return self._default_collection.insert_many(documents, ordered=ordered, ttl=ttl)

    # =====================================================================
    # Read
    # =====================================================================

    def get_all(self) -> List[Dict]:
        return self._default_collection.get_all()

    def get_by_id(self, block_id: str) -> Optional[Dict]:
        return self._default_collection.get_by_id(block_id)

    def get_by_source(self, source_pdf: str) -> List[Dict]:
        return self._default_collection.get_by_source(source_pdf)

    def get_sample(self, n: int = 5) -> List[Dict]:
        return self._default_collection.get_sample(n)

    def find(self, filter: Dict = None, **kwargs) -> List[Dict]:
        return self._default_collection.find(filter, **kwargs)

    def find_one(self, filter: Dict = None, **kwargs) -> Optional[Dict]:
        return self._default_collection.find_one(filter, **kwargs)

    # =====================================================================
    # Update
    # =====================================================================

    def update_one(self, filter: Dict, update: Dict, upsert: bool = False) -> int:
        return self._default_collection.update_one(filter, update, upsert=upsert)

    def update_many(self, filter: Dict, update: Dict, upsert: bool = False) -> int:
        return self._default_collection.update_many(filter, update, upsert=upsert)

    def update_by_id(self, block_id: str, update: Dict, upsert: bool = False) -> int:
        return self._default_collection.update_by_id(block_id, update, upsert=upsert)

    def replace_one(self, filter: Dict, replacement: Dict, upsert: bool = False) -> int:
        return self._default_collection.replace_one(filter, replacement, upsert=upsert)

    # =====================================================================
    # Delete
    # =====================================================================

    def delete_one(self, filter: Dict) -> int:
        return self._default_collection.delete_one(filter)

    def delete_many(self, filter: Dict) -> int:
        return self._default_collection.delete_many(filter)

    def delete_by_id(self, block_id: str) -> int:
        return self._default_collection.delete_by_id(block_id)

    def delete_by_source(self, source_pdf: str) -> int:
        return self._default_collection.delete_by_source(source_pdf)

    def drop(self) -> None:
        self._default_collection.drop()

    # =====================================================================
    # 메타데이터 / 통계
    # =====================================================================

    def get_source_list(self) -> List[str]:
        return self._default_collection.get_source_list()

    def count(self, filter: Dict = None) -> int:
        return self._default_collection.count(filter)

    def count_by_source(self) -> Dict[str, int]:
        return self._default_collection.count_by_source()

    def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        return self._default_collection.aggregate(pipeline)

    # =====================================================================
    # 상태 확인
    # =====================================================================

    def health_check(self) -> Dict:
        return self._client.get("/v1/admin/health")

    # =====================================================================
    # 생명주기 관리
    # =====================================================================

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
                logger.info("MongoDBHandler 연결 종료")
            except Exception as e:
                logger.warning("MongoDBHandler 연결 종료 중 오류: %s", e)

    @classmethod
    def reset(cls) -> None:
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
            logger.info("MongoDBHandler Singleton 리셋 완료")

    # =====================================================================
    # Database / Collection 접근
    # =====================================================================

    def get_collection(self, collection_name: str) -> BlocksCollection:
        """같은 database 내에서 collection만 변경."""
        return BlocksCollection(self._client, collection_name, self.database)

    def get_database(self, database: str, collection: str = None) -> BlocksCollection:
        """다른 database로 전환. collection 미지정 시 기본값 agent_data."""
        col = collection or "agent_data"
        return BlocksCollection(self._client, col, database)

    def get_agent_data(self, database: str | None = None) -> AgentEnvelopeCollection:
        """agent_data envelope 컬렉션 진입점.

        database 인자 미제공 시 현재 핸들러의 database 사용. 반환된 컬렉션은
        모든 read/write가 SERVICE_NAME (X-Caller-Service)으로 자동 스코프됨.
        """
        return AgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_data",
            database=database or self.database,
        )

    def get_agent_output(self, database: str | None = None) -> AgentEnvelopeCollection:
        """agent_output envelope 컬렉션 진입점."""
        return AgentEnvelopeCollection(
            client=self._client,
            collection_name="agent_output",
            database=database or self.database,
        )

    def list_collections(self, database: str = None) -> List[str]:
        """데이터베이스의 컬렉션 목록. database 미지정 시 현재 database 사용."""
        db = database or self.database
        resp = self._client.get("/v1/admin/collections", params={"database": db})
        return resp["collections"]

    # =====================================================================
    # 유틸리티
    # =====================================================================

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return (
            f"<MongoDBHandler database={self.database} "
            f"collection={self.collection_name} status={status}>"
        )
