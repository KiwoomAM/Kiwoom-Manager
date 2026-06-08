# -*- coding: utf-8 -*-
"""
vdb_manager - VDB Handler SDK

사용법:
    from vdb_manager import VDBManager

    handler = VDBManager()

    # Dense
    vector = handler.dense.embed("키움증권 리포트")
    results = handler.dense.search("질문", collection_name="kw_gp")

    # Sparse
    indices, values = handler.sparse.embed("텍스트")
    results = handler.sparse.search("질문", collection_name="kw_gp")

    # Hybrid
    embeddings = handler.hybrid.embed("텍스트")
    results = handler.hybrid.search("질문", collection_name="kw_gp")

    # Collection / CRUD
    handler.collection.create("my_col")
    handler.collection.upsert("my_col", texts=[...], ...)

    # Reranker
    reranked = handler.reranker.rerank("질문", documents=[...])

    # 상태 확인 / 종료
    handler.health_check()
    handler.close()

환경변수:
    VDB_HANDLER_URL: VDB Handler Service URL (기본: http://192.7.9.45:8500)
"""

from .handler import VDBManager
from .dense import DenseClient, DenseEmbedClient
from .sparse import SparseClient, SparseEmbedClient
from .hybrid import HybridClient
from .collection import CollectionClient
from .reranker import RerankerClient
from .exceptions import VDBError, VDBConnectionError, VDBHandlerError

__all__ = [
    "VDBManager",
    "DenseClient",
    "DenseEmbedClient",
    "SparseClient",
    "SparseEmbedClient",
    "HybridClient",
    "CollectionClient",
    "RerankerClient",
    "VDBError",
    "VDBConnectionError",
    "VDBHandlerError",
]
