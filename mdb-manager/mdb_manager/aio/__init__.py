# -*- coding: utf-8 -*-
"""mdb_manager.aio — 비동기 인터페이스.

사용법:
    from mdb_manager.aio import AsyncMongoDBHandler

    async with AsyncMongoDBHandler(database="kw_rag", collection="kw_gp") as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)
"""

from mdb_manager._async_client import AsyncClient
from mdb_manager.aio.collections.agent_envelope import (
    AsyncAgentEnvelopeCollection,
)
from mdb_manager.aio.collections.blocks import AsyncBlocksCollection
from mdb_manager.aio.handler import AsyncMongoDBHandler
from mdb_manager.exceptions import (
    MDBConnectionError,
    MDBError,
    MDBHandlerError,
)

__all__ = [
    "AsyncMongoDBHandler",
    "AsyncBlocksCollection",
    "AsyncAgentEnvelopeCollection",
    "AsyncClient",
    "MDBError",
    "MDBConnectionError",
    "MDBHandlerError",
]
