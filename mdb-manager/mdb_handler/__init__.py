# -*- coding: utf-8 -*-
"""
MongoDB Handler - MongoDB 통합 관리 모듈

사용법:
    from mdb_handler import MongoDBHandler

    mdb = MongoDBHandler()                                    # kw_ai / agent_data
    mdb = MongoDBHandler(database="kw_rag", collection="kw_gp")  # 지정

    # 기본 컬렉션 CRUD
    mdb.get_all()
    mdb.insert_one({"block_id": "p1_b1", ...})

    # 같은 database, 다른 collection
    reports = mdb.get_collection("overseas_reports")

    # 다른 database
    rag = mdb.get_database("kw_rag", "kw_gp")

    # 상태 확인 / 종료
    mdb.health_check()
    mdb.close()
"""

from .handler import MongoDBHandler
from .collections.blocks import BlocksCollection
from .exceptions import MDBError, MDBConnectionError, MDBHandlerError

__all__ = [
    "MongoDBHandler",
    "BlocksCollection",
    "MDBError",
    "MDBConnectionError",
    "MDBHandlerError",
]
