# -*- coding: utf-8 -*-
"""컬렉션 관리 / 포인트 CRUD SDK 클라이언트"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Tuple

from vdb_manager._client import SyncClient


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
        mode: str = "hybrid",
    ) -> Dict[str, str]:
        """컬렉션 생성 (mode: 'dense' | 'hybrid')"""
        return self._client.post(
            f"/v1/collections/{name}",
            json={
                "dense_dim": dense_dim,
                "distance": distance,
                "recreate": recreate,
                "mode": mode,
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
        metadatas: List[Dict[str, Any]],
        sparse_vectors: Optional[List[Tuple[List[int], List[float]]]] = None,
        start_id: int = 0,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """[DEPRECATED] 데이터 업로드 (sparse_vectors 생략 시 dense-only).

        Use ``CollectionClient.upsert_points(..., ids=[...])`` instead.

        서버가 `start_id + j` 로 채번하므로 호출자가 "지금 어디까지 찼는지" 를 정확히
        기억할 때만 안전하다. 기억이 틀리면 에러가 아니라 조용한 덮어쓰기로 나타나고,
        기본값이 `0` 이라 인자 없이 부르면 0번부터 기존 포인트를 지운다. 서버 쪽 경로
        (`/upsert`)도 deprecated 이며 소비자 전환이 끝나면 제거된다.
        """
        warnings.warn(
            "CollectionClient.upsert is deprecated. "
            "Use CollectionClient.upsert_points(..., ids=[...]) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        payload: Dict[str, Any] = {
            "texts": texts,
            "dense_vectors": dense_vectors,
            "metadatas": metadatas,
            "start_id": start_id,
            "batch_size": batch_size,
        }
        if sparse_vectors is not None:
            payload["sparse_vectors"] = [list(sv) for sv in sparse_vectors]
        return self._client.post(
            f"/v1/collections/{collection_name}/upsert",
            json=payload,
        )

    # 하위호환
    upsert_hybrid = upsert

    def upsert_points(
        self,
        collection_name: str,
        texts: List[str],
        dense_vectors: List[List[float]],
        metadatas: List[Dict[str, Any]],
        ids: List[int | str],
        sparse_vectors: Optional[List[Tuple[List[int], List[float]]]] = None,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """포인트 id 를 직접 정해 적재한다 (sparse_vectors 생략 시 dense-only).

        문서에서 유도한 결정론적 id 를 주면 같은 문서의 재적재가 중복이 아니라
        덮어쓰기가 된다. 확인과 적재 사이의 경쟁에서 결과가 달라지는 지점이다.

        id 유도 규칙은 소비자가 정한다 — "같은 문서" 의 정의가 소비자마다 다르다.
        다만 **유도한 id 의 폭을 줄이지 말 것**: 작은 범위로 접으면(`% 1_000_000` 등)
        기존 순번 id 구간을 때려 남의 포인트를 덮어쓴다.

        이것을 존재 확인 수단으로 쓰지 말 것. 기존 포인트는 결정론적 id 를 갖고 있지
        않아 전부 "미적재" 로 답한다. 존재 확인은 `count_points`·`has_points` 로 한다.

        Args:
            collection_name: 대상 컬렉션.
            texts: 원문 목록.
            dense_vectors: 호출자가 이미 계산한 dense 벡터.
            metadatas: 포인트 payload 에 병합할 메타데이터.
            ids: 포인트별 id. 부호 없는 정수 또는 UUID 만 허용되며 형식 검증은 서버가
                한다. `texts` 와 길이가 같아야 한다.
            sparse_vectors: (indices, values) 쌍 목록. None 이면 dense-only.
            batch_size: 서버가 Qdrant 로 한 번에 보낼 포인트 수.

        Returns:
            `{"status": "upserted", "collection": ..., "count": ...}`.
        """
        payload: Dict[str, Any] = {
            "texts": texts,
            "dense_vectors": dense_vectors,
            "metadatas": metadatas,
            "ids": ids,
            "batch_size": batch_size,
        }
        if sparse_vectors is not None:
            payload["sparse_vectors"] = [list(sv) for sv in sparse_vectors]
        return self._client.post(
            f"/v1/collections/{collection_name}/upsert_points",
            json=payload,
        )

    # ------------------------------------------------------------------
    # 포인트 CRUD
    # ------------------------------------------------------------------

    def get_points(
        self,
        collection_name: str,
        ids: List[int | str],
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
        ids: List[int | str],
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
        offset: Optional[int | str] = None,
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

    def count_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> int:
        """조건에 맞는 포인트 수를 센다. 벡터도 query 텍스트도 필요 없다.

        존재 확인용 경로다. 서버가 정확한 개수로 고정하며 근사치를 고를 인자는 없다 —
        근사치는 0 과 1 을 헷갈려 중복 적재를 막자는 목적 자체를 무너뜨린다.

        Args:
            collection_name: 대상 컬렉션.
            payload_filter: `{"must": [{"key": ..., "match": {"value": ...}}]}` 형식.
                None 이면 컬렉션 전체 개수. 지원하는 절과 조건의 정의는 서버의
                `vdb-handler/app/core/vector_store.py::_build_qdrant_filter` 가 단일
                출처다. 모르는 절을 보내면 서버가 422 로 거부한다.

        Returns:
            조건에 맞는 포인트 수.
        """
        resp = self._client.post(
            f"/v1/collections/{collection_name}/count",
            json={"payload_filter": payload_filter},
        )
        return resp["count"]

    def has_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> bool:
        """조건에 맞는 포인트가 하나라도 있는지 본다.

        `count_points` 위의 얇은 래퍼다. 개수에서 존재는 유도되지만 역은 안 된다.

        이것을 "적재 완료" 로 읽지 말 것. 문서 하나가 포인트 여럿이 되는 소비자
        (청킹)에서는 일부만 들어간 상태도 True 라, 부분 적재가 영구히 굳는다.

        Args:
            collection_name: 대상 컬렉션.
            payload_filter: `count_points` 와 같은 형식.

        Returns:
            조건에 맞는 포인트가 하나 이상이면 True.
        """
        return self.count_points(collection_name, payload_filter) > 0

    @property
    def is_ready(self) -> bool:
        """Qdrant 연결 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["qdrant"].get("status") == "connected"
        except Exception:
            return False
