# -*- coding: utf-8 -*-
"""
MongoDB Handler - MongoDB 통합 관리 모듈

사용법:
    from mdb_handler import MongoDBHandler

    mdb = MongoDBHandler()

    # 기본 컬렉션 CRUD (직접 접근)
    mdb.get_all()
    mdb.insert_one({"block_id": "p1_b1", ...})
    mdb.update_by_id("p1_b1", {"$set": {"content.text": "변경"}})
    mdb.delete_by_id("p1_b1")

    # 다른 컬렉션 접근
    reports = mdb.get_collection("overseas_reports")

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
