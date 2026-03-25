# -*- coding: utf-8 -*-
"""컬렉션 관리 / 포인트 CRUD SDK 클라이언트"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from vdb_handler._client import SyncClient


class CollectionClient:
    """Service의 /v1/collections 엔드포인트를 호출하는 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # 컬렉션 관리
    # ------------------------------------------------------------------

    def create(
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

    # 하위호환
    create_collection = create

    def info(self, name: str) -> Dict[str, Any]:
        """컬렉션 정보 조회"""
        return self._client.get(f"/v1/collections/{name}")

    # 하위호환
    get_collection_info = info

    def delete(self, name: str) -> Dict[str, str]:
        """컬렉션 삭제"""
        return self._client.delete(f"/v1/collections/{name}")

    # 하위호환
    delete_collection = delete

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
        """하이브리드 데이터 업로드 (dense + sparse 필수)"""
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

    # 하위호환
    upsert_hybrid = upsert

    # ------------------------------------------------------------------
    # 포인트 CRUD
    # ------------------------------------------------------------------

    def get_points(
        self,
        collection_name: str,
        ids: List[int],
        with_vectors: bool = False,
    ) -> List[Dict[str, Any]]:
        """ID로 포인트 조회"""
        resp = self._client.post(
            f"/v1/collections/{collection_name}/points",
            json={"ids": ids, "with_vectors": with_vectors},
        )
        return resp["points"]

    def delete_points(
        self,
        collection_name: str,
        ids: List[int],
    ) -> Dict[str, Any]:
        """ID로 포인트 삭제"""
        return self._client.delete(
            f"/v1/collections/{collection_name}/points",
            json={"ids": ids},
        )

    def scroll_points(
        self,
        collection_name: str,
        limit: int = 20,
        offset: Optional[int] = None,
        with_vectors: bool = False,
    ) -> Dict[str, Any]:
        """포인트 스크롤 조회 (페이지네이션)"""
        return self._client.post(
            f"/v1/collections/{collection_name}/scroll",
            json={
                "limit": limit,
                "offset": offset,
                "with_vectors": with_vectors,
            },
        )

    @property
    def is_ready(self) -> bool:
        """Qdrant 연결 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["qdrant"].get("status") == "connected"
        except Exception:
            return False
