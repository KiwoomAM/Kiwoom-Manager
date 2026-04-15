# -*- coding: utf-8 -*-
"""mdb_handler.aio — 비동기 인터페이스.

사용법:
    from mdb_handler.aio import AsyncMongoDBHandler

    async with AsyncMongoDBHandler(database="kw_rag", collection="kw_gp") as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)
"""

from mdb_handler._async_client import AsyncClient
from mdb_handler.aio.collections.blocks import AsyncBlocksCollection
from mdb_handler.aio.handler import AsyncMongoDBHandler
from mdb_handler.exceptions import (
    MDBConnectionError,
    MDBError,
    MDBHandlerError,
)

__all__ = [
    "AsyncMongoDBHandler",
    "AsyncBlocksCollection",
    "AsyncClient",
    "MDBError",
    "MDBConnectionError",
    "MDBHandlerError",
]
