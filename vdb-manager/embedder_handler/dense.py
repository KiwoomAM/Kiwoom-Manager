# -*- coding: utf-8 -*-
"""Dense 임베딩 SDK 클라이언트"""

from __future__ import annotations

from typing import List

from embedder_handler._client import SyncClient


class DenseEmbedClient:
    """Service의 /v1/embed 엔드포인트를 호출하는 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def embed_text(
        self, text: str, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> List[float]:
        """단일 텍스트 dense 임베딩"""
        resp = self._client.post(
            "/v1/embed",
            json={"text": text, "task_type": task_type},
        )
        return resp["embedding"]

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

    def get_embedding_dimension(self) -> int:
        """임베딩 차원 조회 (admin/status에서 가져옴)"""
        resp = self._client.get("/v1/admin/status")
        return resp["dense"]["dimension"]

    get_dimension = get_embedding_dimension
