# -*- coding: utf-8 -*-
"""Dense 임베딩 / 검색 SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from vdb_handler._client import SyncClient


class DenseClient:
    """Dense 모델 관련 동작 (임베딩 + 검색)"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def embed(
        self, text: str, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> List[float]:
        """단일 텍스트 dense 임베딩"""
        resp = self._client.post(
            "/v1/embed",
            json={"text": text, "task_type": task_type},
        )
        return resp["embedding"]

    # 하위호환
    embed_text = embed

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[List[float]]:
        """배치 dense 임베딩"""
        resp = self._client.post(
            "/v1/embed/batch",
            json={
                "texts": texts,
                "batch_size": batch_size,
                "task_type": task_type,
            },
        )
        return resp["embeddings"]

    def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Dense 벡터 단독 검색"""
        resp = self._client.post(
            "/v1/search/dense",
            json={
                "query": query,
                "collection_name": collection_name,
                "top_k": top_k,
                "score_threshold": score_threshold,
                "payload_filter": payload_filter,
            },
        )
        return resp["results"]

    def get_embedding_dimension(self) -> int:
        """임베딩 차원 조회 (admin/status에서 가져옴)"""
        resp = self._client.get("/v1/admin/status")
        return resp["dense"]["dimension"]

    get_dimension = get_embedding_dimension

    @property
    def is_ready(self) -> bool:
        """Dense 모델 준비 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["dense"].get("status") == "ready"
        except Exception:
            return False


# 하위호환
DenseEmbedClient = DenseClient
