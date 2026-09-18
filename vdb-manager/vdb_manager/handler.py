# -*- coding: utf-8 -*-
"""
VDBManager - 통합 진입점 (Singleton)

사용 예시:
    from vdb_manager import VDBManager

    handler = VDBManager()

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
    if not handler.collection.has_points("my_col", {"must": [...]}):
        handler.collection.upsert_points("my_col", texts=[...], ids=[...], ...)
    handler.collection.get_points("my_col", ids=[1, 2])

    # Reranker
    reranked = handler.reranker.rerank("질문", documents=[...])

    # 상태 확인 / 종료
    handler.health_check()
    handler.close()
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

from vdb_manager._client import SyncClient
from vdb_manager.dense import DenseClient
from vdb_manager.sparse import SparseClient
from vdb_manager.hybrid import HybridClient
from vdb_manager.collection import CollectionClient
from vdb_manager.reranker import RerankerClient

logger = logging.getLogger(__name__)


class VDBManager:
    """VDB Handler 통합 인터페이스 (Singleton)"""

    _instance: Optional["VDBManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, timeout: float = 120.0):
        if self._initialized:
            return

        try:
            self._client = SyncClient(timeout=timeout)
            self.dense = DenseClient(self._client)
            self.sparse = SparseClient(self._client)
            self.hybrid = HybridClient(
                self._client, dense=self.dense, sparse=self.sparse
            )
            self.collection = CollectionClient(self._client)
            self.reranker = RerankerClient(self._client)
        except Exception:
            # 생성 실패 시 Singleton 캐시를 비워 다음 생성이 정상 재초기화되도록 한다.
            type(self)._instance = None
            raise

        self._initialized = True
        logger.info("VDBManager 초기화 완료")

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
        method: str = "colbert",
        **gemini_kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """문서 리랭킹 (method: "colbert" 서버 위임 | "gemini" LLM 리랭킹)"""
        return self.reranker.rerank(
            query=query,
            documents=documents,
            top_k=top_k,
            text_key=text_key,
            method=method,
            **gemini_kwargs,
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
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Dense + Sparse 하이브리드 검색"""
        return self.hybrid.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            use_rerank=use_rerank,
            rerank_top_k=rerank_top_k,
            payload_filter=payload_filter,
        )

    def dense_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Dense 벡터 단독 검색"""
        return self.dense.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            score_threshold=score_threshold,
            payload_filter=payload_filter,
        )

    def sparse_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        score_threshold: Optional[float] = None,
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Sparse 벡터 단독 검색 (SPLADE)"""
        return self.sparse.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            score_threshold=score_threshold,
            payload_filter=payload_filter,
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
        mode: str = "hybrid",
    ) -> Dict[str, str]:
        """컬렉션 생성 (mode: 'dense' | 'hybrid')"""
        return self.collection.create(
            name=name, dense_dim=dense_dim, distance=distance, recreate=recreate, mode=mode
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
        metadatas: List[Dict[str, Any]],
        sparse_vectors: Optional[List[Tuple[List[int], List[float]]]] = None,
        start_id: int = 0,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """[DEPRECATED] 데이터 업로드 (sparse_vectors 생략 시 dense-only).

        Use ``VDBManager.upsert_points(..., ids=[...])`` instead.
        근거는 `CollectionClient.upsert` docstring 에 있다.
        """
        warnings.warn(
            "VDBManager.upsert is deprecated. "
            "Use VDBManager.upsert_points(..., ids=[...]) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # 경고를 여기서 냈으므로 공개 메서드를 다시 부르지 않는다. 두 번 경고하면
        # 둘 중 하나가 SDK 내부 줄을 가리켜 호출자가 자기 코드를 못 찾는다.
        return self.collection._upsert_legacy(
            collection_name=collection_name,
            texts=texts,
            dense_vectors=dense_vectors,
            metadatas=metadatas,
            sparse_vectors=sparse_vectors,
            start_id=start_id,
            batch_size=batch_size,
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
        """포인트 id 를 직접 정해 적재"""
        return self.collection.upsert_points(
            collection_name=collection_name,
            texts=texts,
            dense_vectors=dense_vectors,
            metadatas=metadatas,
            ids=ids,
            sparse_vectors=sparse_vectors,
            batch_size=batch_size,
        )

    # ------------------------------------------------------------------
    # 포인트 CRUD (바로가기)
    # ------------------------------------------------------------------

    def get_points(
        self,
        collection_name: str,
        ids: List[int | str],
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
        ids: List[int | str],
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
        offset: Optional[int | str] = None,
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

    def count_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> int:
        """조건에 맞는 포인트 수 조회"""
        return self.collection.count_points(
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    def has_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> bool:
        """조건에 맞는 포인트가 하나라도 있는지 확인"""
        return self.collection.has_points(
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    # ------------------------------------------------------------------
    # payload 수정 / 색인
    # ------------------------------------------------------------------

    def set_payload(
        self,
        collection_name: str,
        ids: List[int | str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """payload 에 주는 키만 더하거나 덮기 (나머지 키 보존)"""
        return self.collection.set_payload(
            collection_name=collection_name,
            ids=ids,
            payload=payload,
        )

    def overwrite_payload(
        self,
        collection_name: str,
        ids: List[int | str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """payload 를 통째로 교체 (기존 키는 사라짐)"""
        return self.collection.overwrite_payload(
            collection_name=collection_name,
            ids=ids,
            payload=payload,
        )

    def delete_payload_keys(
        self,
        collection_name: str,
        ids: List[int | str],
        keys: List[str],
    ) -> Dict[str, Any]:
        """payload 에서 주는 키만 삭제"""
        return self.collection.delete_payload_keys(
            collection_name=collection_name,
            ids=ids,
            keys=keys,
        )

    def clear_payload(
        self,
        collection_name: str,
        ids: List[int | str],
    ) -> Dict[str, Any]:
        """payload 를 통째로 비우기"""
        return self.collection.clear_payload(
            collection_name=collection_name,
            ids=ids,
        )

    def create_payload_index(
        self,
        collection_name: str,
        field: str,
        schema: str,
    ) -> Dict[str, Any]:
        """payload 필드에 색인 생성"""
        return self.collection.create_payload_index(
            collection_name=collection_name,
            field=field,
            schema=schema,
        )

    def list_payload_indexes(self, collection_name: str) -> Dict[str, str]:
        """필드별 색인 현황 조회"""
        return self.collection.list_payload_indexes(
            collection_name=collection_name,
        )

    def delete_payload_index(
        self,
        collection_name: str,
        field: str,
    ) -> Dict[str, Any]:
        """payload 색인 삭제"""
        return self.collection.delete_payload_index(
            collection_name=collection_name,
            field=field,
        )

    def health_check(self) -> Dict[str, Any]:
        """서비스 상태 확인"""
        return self._client.get("/v1/admin/status")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
                logger.info("VDBManager 연결 종료")
            except Exception as e:
                logger.warning("VDBManager 연결 종료 중 오류: %s", e)

    @classmethod
    def reset(cls) -> None:
        """싱글톤 리셋"""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
            logger.info("VDBManager Singleton 리셋 완료")

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return f"<VDBManager status={status}>"
