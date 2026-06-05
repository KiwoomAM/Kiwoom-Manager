# -*- coding: utf-8 -*-
"""
MongoDB Handler - MongoDB 통합 관리 모듈

사용법 (동기):
    from mdb_manager import MongoDBHandler

    mdb = MongoDBHandler()
    mdb.get_all()
    mdb.insert_one({"block_id": "p1_b1", ...})
    mdb.close()

사용법 (비동기):
    from mdb_manager.aio import AsyncMongoDBHandler

    async with AsyncMongoDBHandler() as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)
"""

from mdb_manager import aio
from mdb_manager.collections.agent_envelope import AgentEnvelopeCollection
from mdb_manager.collections.blocks import BlocksCollection
from mdb_manager.exceptions import (
    MDBConfigError,
    MDBConnectionError,
    MDBError,
    MDBHandlerError,
)
from mdb_manager.handler import MongoDBHandler

__all__ = [
    "MongoDBHandler",
    "BlocksCollection",
    "AgentEnvelopeCollection",
    "MDBError",
    "MDBConnectionError",
    "MDBHandlerError",
    "MDBConfigError",
    "aio",
]
