# -*- coding: utf-8 -*-
"""Sparse 임베딩 SDK 클라이언트"""

from __future__ import annotations

from typing import List, Tuple

from vdb_handler._client import SyncClient


class SparseEmbedClient:
    """Service의 /v1/sparse 엔드포인트를 호출하는 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def encode(self, text: str) -> Tuple[List[int], List[float]]:
        """단일 텍스트 sparse 인코딩"""
        resp = self._client.post(
            "/v1/sparse/encode",
            json={"text": text},
        )
        return resp["indices"], resp["values"]

    def encode_batch(
        self,
        texts: List[str],
        batch_size: int = 8,
    ) -> List[Tuple[List[int], List[float]]]:
        """배치 sparse 인코딩"""
        resp = self._client.post(
            "/v1/sparse/encode/batch",
            json={"texts": texts, "batch_size": batch_size},
        )
        return [
            (item["indices"], item["values"])
            for item in resp["results"]
        ]

    @property
    def is_ready(self) -> bool:
        """SPLADE 모델 준비 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["sparse"].get("status") == "ready"
        except Exception:
            return False
