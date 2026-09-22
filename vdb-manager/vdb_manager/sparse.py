# -*- coding: utf-8 -*-
"""Sparse 임베딩 / 검색 SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from vdb_manager._client import SyncClient
from vdb_manager._guards import reject_empty_filter


class SparseClient:
    """Sparse 모델 관련 동작 (임베딩 + 검색)"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def embed(self, text: str) -> Tuple[List[int], List[float]]:
        """단일 텍스트 sparse 임베딩 (SPLADE)"""
        resp = self._client.post(
            "/v1/embed/sparse",
            json={"text": text},
        )
        return resp["indices"], resp["values"]

    # 하위호환
    encode = embed

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 8,
    ) -> List[Tuple[List[int], List[float]]]:
        """배치 sparse 임베딩 (SPLADE)"""
        resp = self._client.post(
            "/v1/embed/sparse/batch",
            json={"texts": texts, "batch_size": batch_size},
        )
        return [
            (item["indices"], item["values"])
            for item in resp["results"]
        ]

    # 하위호환
    encode_batch = embed_batch

    def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Sparse 벡터 단독 검색 (SPLADE)

        Raises:
            ValueError: payload_filter 가 빈 dict 일 때. `{}` 는 조건 없음이 아니라
                조건을 만들려다 비어서 나온 상태다. 조건 없이 전체를 대상으로 하려면
                인자를 생략한다.
        """
        reject_empty_filter(payload_filter)
        resp = self._client.post(
            "/v1/search/sparse",
            json={
                "query": query,
                "collection_name": collection_name,
                "top_k": top_k,
                "score_threshold": score_threshold,
                "payload_filter": payload_filter,
            },
        )
        return resp["results"]

    @property
    def is_ready(self) -> bool:
        """SPLADE 모델 준비 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["sparse"].get("status") == "ready"
        except Exception:
            return False


# 하위호환
SparseEmbedClient = SparseClient
