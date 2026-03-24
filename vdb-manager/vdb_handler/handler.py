# -*- coding: utf-8 -*-
"""
VDBHandler - 통합 진입점 (Singleton)

사용 예시:
    from vdb_handler import VDBHandler

    handler = VDBHandler()

    # Dense 임베딩
    vector = handler.embed_text("키움증권 리포트")
    vectors = handler.embed_batch(["텍스트1", "텍스트2"])

    # Sparse 임베딩
    indices, values = handler.sparse_encode("텍스트")

    # 하이브리드 검색
    results = handler.hybrid_search("질문", collection_name="kw_gp")

    # 리랭킹
    reranked = handler.rerank("질문", documents=[...])

    # 상태 확인 / 종료
    handler.health_check()
    handler.close()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from vdb_handler._client import SyncClient
from vdb_handler.dense import DenseEmbedClient
from vdb_handler.sparse import SparseEmbedClient
from vdb_handler.reranker import RerankerClient
from vdb_handler.search import SearchClient

logger = logging.getLogger(__name__)


class VDBHandler:
    """VDB Handler 통합 인터페이스 (Singleton)"""

    _instance: Optional["VDBHandler"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, timeout: float = 120.0):
        if self._initialized:
            return

        self._client = SyncClient(timeout=timeout)
        self.dense = DenseEmbedClient(self._client)
        self.sparse = SparseEmbedClient(self._client)
        self.reranker = RerankerClient(self._client)
        self.search = SearchClient(self._client)

        self._initialized = True
        logger.info("VDBHandler 초기화 완료")

    # ------------------------------------------------------------------
    # Dense 임베딩 (바로가기)
    # ------------------------------------------------------------------

    def embed_text(
        self, text: str, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> List[float]:
        """단일 텍스트 dense 임베딩"""
        return self.dense.embed_text(text, task_type=task_type)

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[List[float]]:
        """배치 dense 임베딩"""
        return self.dense.embed_batch(texts, batch_size=batch_size, task_type=task_type)

    # ------------------------------------------------------------------
    # Sparse 임베딩 (바로가기)
    # ------------------------------------------------------------------

    def sparse_encode(self, text: str) -> Tuple[List[int], List[float]]:
        """단일 텍스트 sparse 인코딩"""
        return self.sparse.encode(text)

    def sparse_encode_batch(
        self, texts: List[str], batch_size: int = 8
    ) -> List[Tuple[List[int], List[float]]]:
        """배치 sparse 인코딩"""
        return self.sparse.encode_batch(texts, batch_size=batch_size)

    # ------------------------------------------------------------------
    # 리랭킹 (바로가기)
    # ------------------------------------------------------------------

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
        text_key: str = "text",
    ) -> List[Dict[str, Any]]:
        """ColBERT v2 기반 문서 리랭킹"""
        return self.reranker.rerank(
            query=query, documents=documents, top_k=top_k, text_key=text_key
        )

    # ------------------------------------------------------------------
    # 하이브리드 검색 (바로가기)
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        use_rerank: bool = True,
        rerank_top_k: int = 50,
        block_type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dense + Sparse 하이브리드 검색"""
        return self.search.hybrid_search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            use_rerank=use_rerank,
            rerank_top_k=rerank_top_k,
            block_type_filter=block_type_filter,
        )

    # ------------------------------------------------------------------
    # 상태 확인 / 생명주기
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        """서비스 상태 확인"""
        return self._client.get("/v1/admin/status")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
                logger.info("VDBHandler 연결 종료")
            except Exception as e:
                logger.warning("VDBHandler 연결 종료 중 오류: %s", e)

    @classmethod
    def reset(cls) -> None:
        """싱글톤 리셋"""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
            logger.info("VDBHandler Singleton 리셋 완료")

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return f"<VDBHandler status={status}>"
