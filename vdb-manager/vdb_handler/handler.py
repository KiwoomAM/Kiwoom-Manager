# -*- coding: utf-8 -*-
"""
VDBHandler - 통합 진입점 (Singleton)

사용 예시:
    from vdb_handler import VDBHandler

    handler = VDBHandler()

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
    handler.collection.get_points("my_col", ids=[1, 2])

    # Reranker
    reranked = handler.reranker.rerank("질문", documents=[...])

    # 상태 확인 / 종료
    handler.health_check()
    handler.close()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from vdb_handler._client import SyncClient
from vdb_handler.dense import DenseClient
from vdb_handler.sparse import SparseClient
from vdb_handler.hybrid import HybridClient
from vdb_handler.collection import CollectionClient
from vdb_handler.reranker import RerankerClient

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
        self.dense = DenseClient(self._client)
        self.sparse = SparseClient(self._client)
        self.hybrid = HybridClient(self._client, dense=self.dense, sparse=self.sparse)
        self.collection = CollectionClient(self._client)
        self.reranker = RerankerClient(self._client)

        self._initialized = True
        logger.info("VDBHandler 초기화 완료")

    # ------------------------------------------------------------------
    # Dense 임베딩 (바로가기)
    # ------------------------------------------------------------------

    def embed(
        self, text: str, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> List[float]:
        """단일 텍스트 dense 임베딩"""
        return self.dense.embed(text, task_type=task_type)

    # 하위호환
    embed_text = embed

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

    def sparse_embed(self, text: str) -> Tuple[List[int], List[float]]:
        """단일 텍스트 sparse 임베딩"""
        return self.sparse.embed(text)

    # 하위호환
    sparse_encode = sparse_embed

    def sparse_embed_batch(
        self, texts: List[str], batch_size: int = 8
    ) -> List[Tuple[List[int], List[float]]]:
        """배치 sparse 임베딩"""
        return self.sparse.embed_batch(texts, batch_size=batch_size)

    # 하위호환
    sparse_encode_batch = sparse_embed_batch

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
    # 검색 (바로가기)
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
        return self.hybrid.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            use_rerank=use_rerank,
            rerank_top_k=rerank_top_k,
            block_type_filter=block_type_filter,
        )

    def dense_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        block_type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dense 벡터 단독 검색"""
        return self.dense.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            score_threshold=score_threshold,
            block_type_filter=block_type_filter,
        )

    def sparse_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        block_type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Sparse 벡터 단독 검색 (SPLADE)"""
        return self.sparse.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            score_threshold=score_threshold,
            block_type_filter=block_type_filter,
        )

    # ------------------------------------------------------------------
    # 컬렉션 관리 (바로가기)
    # ------------------------------------------------------------------

    def create_collection(
        self,
        name: str,
        dense_dim: int = 1536,
        distance: str = "Cosine",
        recreate: bool = False,
    ) -> Dict[str, str]:
        """하이브리드 컬렉션 생성"""
        return self.collection.create(
            name=name, dense_dim=dense_dim, distance=distance, recreate=recreate
        )

    def get_collection_info(self, name: str) -> Dict[str, Any]:
        """컬렉션 정보 조회"""
        return self.collection.info(name)

    def delete_collection(self, name: str) -> Dict[str, str]:
        """컬렉션 삭제"""
        return self.collection.delete(name)

    def upsert(
        self,
        collection_name: str,
        texts: List[str],
        dense_vectors: List[List[float]],
        sparse_vectors: List[Tuple[List[int], List[float]]],
        metadatas: List[Dict[str, Any]],
        start_id: int = 0,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """하이브리드 데이터 업로드"""
        return self.collection.upsert(
            collection_name=collection_name,
            texts=texts,
            dense_vectors=dense_vectors,
            sparse_vectors=sparse_vectors,
            metadatas=metadatas,
            start_id=start_id,
            batch_size=batch_size,
        )

    # 하위호환
    upsert_hybrid = upsert

    # ------------------------------------------------------------------
    # 포인트 CRUD (바로가기)
    # ------------------------------------------------------------------

    def get_points(
        self,
        collection_name: str,
        ids: List[int],
        with_vectors: bool = False,
    ) -> List[Dict[str, Any]]:
        """ID로 포인트 조회"""
        return self.collection.get_points(
            collection_name=collection_name,
            ids=ids,
            with_vectors=with_vectors,
        )

    def delete_points(
        self,
        collection_name: str,
        ids: List[int],
    ) -> Dict[str, Any]:
        """ID로 포인트 삭제"""
        return self.collection.delete_points(
            collection_name=collection_name,
            ids=ids,
        )

    def scroll_points(
        self,
        collection_name: str,
        limit: int = 20,
        offset: Optional[int] = None,
        with_vectors: bool = False,
    ) -> Dict[str, Any]:
        """포인트 스크롤 조회 (페이지네이션)"""
        return self.collection.scroll_points(
            collection_name=collection_name,
            limit=limit,
            offset=offset,
            with_vectors=with_vectors,
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
