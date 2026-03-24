# -*- coding: utf-8 -*-
"""리랭킹 SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List

from embedder_handler._client import SyncClient


class RerankerClient:
    """Service의 /v1/rerank 엔드포인트를 호출하는 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
        text_key: str = "text",
    ) -> List[Dict[str, Any]]:
        """ColBERT v2 기반 문서 리랭킹"""
        resp = self._client.post(
            "/v1/rerank",
            json={
                "query": query,
                "documents": documents,
                "top_k": top_k,
                "text_key": text_key,
            },
        )
        return resp["documents"]

    @property
    def is_ready(self) -> bool:
        """ColBERT 모델 준비 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["colbert"].get("status") == "ready"
        except Exception:
            return False
