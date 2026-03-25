# -*- coding: utf-8 -*-
"""Hybrid (Dense + Sparse) 통합 SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from vdb_handler._client import SyncClient
from vdb_handler.dense import DenseClient
from vdb_handler.sparse import SparseClient


class HybridClient:
    """Hybrid (Dense + Sparse) 통합 클라이언트 — 임베딩 + 검색"""

    def __init__(
        self,
        client: SyncClient,
        dense: DenseClient,
        sparse: SparseClient,
    ) -> None:
        self._client = client
        self._dense = dense
        self._sparse = sparse

    # ------------------------------------------------------------------
    # 하이브리드 임베딩
    # ------------------------------------------------------------------

    def embed(
        self,
        text: str,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> Dict[str, Any]:
        """단일 텍스트 하이브리드 임베딩 (dense + sparse)

        Returns:
            {"dense": List[float], "sparse": (List[int], List[float])}
        """
        dense_vector = self._dense.embed_text(text, task_type=task_type)
        sparse_vector = self._sparse.encode(text)
        return {
            "dense": dense_vector,
            "sparse": sparse_vector,
        }

    def embed_batch(
        self,
        texts: List[str],
        dense_batch_size: int = 100,
        sparse_batch_size: int = 8,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> Dict[str, Any]:
        """배치 하이브리드 임베딩 (dense + sparse)

        Returns:
            {"dense": List[List[float]], "sparse": List[Tuple[List[int], List[float]]]}
        """
        dense_vectors = self._dense.embed_batch(
            texts, batch_size=dense_batch_size, task_type=task_type
        )
        sparse_vectors = self._sparse.encode_batch(
            texts, batch_size=sparse_batch_size
        )
        return {
            "dense": dense_vectors,
            "sparse": sparse_vectors,
        }

    # ------------------------------------------------------------------
    # 하이브리드 검색
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        use_rerank: bool = True,
        rerank_top_k: int = 50,
        block_type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dense + Sparse 하이브리드 검색"""
        resp = self._client.post(
            "/v1/search/hybrid",
            json={
                "query": query,
                "collection_name": collection_name,
                "top_k": top_k,
                "use_rerank": use_rerank,
                "rerank_top_k": rerank_top_k,
                "block_type_filter": block_type_filter,
            },
        )
        return resp["results"]

    @property
    def is_ready(self) -> bool:
        """Dense + Sparse 모두 준비되었는지 확인"""
        return self._dense.is_ready and self._sparse.is_ready
