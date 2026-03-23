# -*- coding: utf-8 -*-
"""
MongoDBHandler - HTTP 클라이언트 래퍼 (Singleton)

기존 pymongo 기반 MongoDBHandler와 동일한 인터페이스를 제공합니다.
내부적으로 MDB Handler Service (localhost:8300)에 HTTP 요청을 보냅니다.

사용 예시:
    from mdb_handler import MongoDBHandler

    mdb = MongoDBHandler()

    mdb.get_all()
    mdb.insert_one({"block_id": "p1_b1", ...})
    mdb.find({"_metadata.type": "table"})

    reports = mdb.get_collection("overseas_reports")
    reports.get_all()

    mdb.health_check()
    mdb.close()
"""

import logging
import warnings
from typing import Dict, List, Optional

from mdb_handler._client import SyncClient
from mdb_handler.collections.blocks import BlocksCollection

logger = logging.getLogger(__name__)


class MongoDBHandler:
    """
    MongoDB 통합 핸들러 (Singleton) -- HTTP 클라이언트 래퍼.

    MDB Handler Service를 통해 MongoDB에 접근합니다.
    기존과 동일한 메서드 시그니처를 유지합니다.
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
        if database is not None:
            warnings.warn(
                "database 파라미터는 무시됩니다. Service 측 설정을 사용합니다.",
                DeprecationWarning,
                stacklevel=2,
            )

        self.collection_name = collection or "kw_gp"
        self._client = SyncClient()
        self._default_collection = BlocksCollection(
            self._client, self.collection_name
        )

        self._initialized = True
        logger.info(
            "MongoDBHandler 초기화 완료 (collection=%s)", self.collection_name
        )

    # =====================================================================
    # Create
    # =====================================================================

    def insert_one(self, document: Dict) -> str:
        return self._default_collection.insert_one(document)

    def insert_many(
        self, documents: List[Dict], ordered: bool = True
    ) -> List[str]:
        return self._default_collection.insert_many(documents, ordered=ordered)

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

    def update_one(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        return self._default_collection.update_one(
            filter, update, upsert=upsert
        )

    def update_many(
        self, filter: Dict, update: Dict, upsert: bool = False
    ) -> int:
        return self._default_collection.update_many(
            filter, update, upsert=upsert
        )

    def update_by_id(
        self, block_id: str, update: Dict, upsert: bool = False
    ) -> int:
        return self._default_collection.update_by_id(
            block_id, update, upsert=upsert
        )

    def replace_one(
        self, filter: Dict, replacement: Dict, upsert: bool = False
    ) -> int:
        return self._default_collection.replace_one(
            filter, replacement, upsert=upsert
        )

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
    # Collection 접근
    # =====================================================================

    def get_collection(
        self,
        collection_name: str,
        database: str = None,
    ) -> BlocksCollection:
        if database is not None:
            warnings.warn(
                "database 파라미터는 무시됩니다. Service 측 설정을 사용합니다.",
                DeprecationWarning,
                stacklevel=2,
            )
        return BlocksCollection(self._client, collection_name)

    def list_collections(self, database: str = None) -> List[str]:
        if database is not None:
            warnings.warn(
                "database 파라미터는 무시됩니다. Service 측 설정을 사용합니다.",
                DeprecationWarning,
                stacklevel=2,
            )
        resp = self._client.get("/v1/admin/collections")
        return resp["collections"]

    # =====================================================================
    # 유틸리티
    # =====================================================================

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return (
            f"<MongoDBHandler collection={self.collection_name} "
            f"status={status}>"
        )
