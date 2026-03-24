# -*- coding: utf-8 -*-
"""
embedder_handler - Embedder Handler SDK

사용법:
    from embedder_handler import EmbedderHandler

    handler = EmbedderHandler()

    # Dense 임베딩
    vector = handler.embed_text("키움증권 리포트")

    # Sparse 임베딩
    indices, values = handler.sparse_encode("텍스트")

    # 하이브리드 검색
    results = handler.hybrid_search("질문", collection_name="kw_gp")

    # 리랭킹
    reranked = handler.rerank("질문", documents=[...])

    # 상태 확인 / 종료
    handler.health_check()
    handler.close()

환경변수:
    EMBEDDER_HANDLER_URL: Embedder Handler Service URL (기본: http://192.7.9.45:8500)
"""

from .handler import EmbedderHandler
from .dense import DenseEmbedClient
from .sparse import SparseEmbedClient
from .reranker import RerankerClient
from .search import SearchClient
from .exceptions import EmbedderError, EmbedderConnectionError, EmbedderHandlerError

__all__ = [
    "EmbedderHandler",
    "DenseEmbedClient",
    "SparseEmbedClient",
    "RerankerClient",
    "SearchClient",
    "EmbedderError",
    "EmbedderConnectionError",
    "EmbedderHandlerError",
]
