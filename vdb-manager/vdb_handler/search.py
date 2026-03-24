# -*- coding: utf-8 -*-
"""하이브리드 검색 / 컬렉션 관리 SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from vdb_handler._client import SyncClient


class SearchClient:
    """Service의 /v1/search, /v1/collections 엔드포인트를 호출하는 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

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

    def create_collection(
        self,
        name: str,
        dense_dim: int = 1536,
        distance: str = "Cosine",
        recreate: bool = False,
    ) -> Dict[str, str]:
        """하이브리드 컬렉션 생성"""
        return self._client.post(
            f"/v1/collections/{name}",
            json={
                "dense_dim": dense_dim,
                "distance": distance,
                "recreate": recreate,
            },
        )

    def get_collection_info(self, name: str) -> Dict[str, Any]:
        """컬렉션 정보 조회"""
        return self._client.get(f"/v1/collections/{name}")

    def delete_collection(self, name: str) -> Dict[str, str]:
        """컬렉션 삭제"""
        return self._client.delete(f"/v1/collections/{name}")

    def upsert_hybrid(
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
        sv_serialized = [list(sv) for sv in sparse_vectors]
        return self._client.post(
            f"/v1/collections/{collection_name}/upsert",
            json={
                "texts": texts,
                "dense_vectors": dense_vectors,
                "sparse_vectors": sv_serialized,
                "metadatas": metadatas,
                "start_id": start_id,
                "batch_size": batch_size,
            },
        )
