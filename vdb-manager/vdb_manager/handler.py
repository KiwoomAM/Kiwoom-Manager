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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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

    # >>> 여기부터 생성된 바로가기 — scripts/generate_shortcuts.py 가 쓴다. 손으로 고치지 말 것

    def embed(
        self,
        text: str,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[float]:
        """`DenseClient.embed` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.dense.embed(
            text=text,
            task_type=task_type,
        )

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int = 100,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[List[float]]:
        """`DenseClient.embed_batch` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.dense.embed_batch(
            texts=texts,
            batch_size=batch_size,
            task_type=task_type,
        )

    def get_embedding_dimension(self) -> int:
        """`DenseClient.get_embedding_dimension` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.dense.get_embedding_dimension()

    def sparse_embed(
        self,
        text: str,
    ) -> Tuple[List[int], List[float]]:
        """`SparseClient.embed` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.sparse.embed(
            text=text,
        )

    def sparse_embed_batch(
        self,
        texts: List[str],
        batch_size: int = 8,
    ) -> List[Tuple[List[int], List[float]]]:
        """`SparseClient.embed_batch` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.sparse.embed_batch(
            texts=texts,
            batch_size=batch_size,
        )

    def hybrid_embed(
        self,
        text: str,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> Dict[str, Any]:
        """`HybridClient.embed` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.hybrid.embed(
            text=text,
            task_type=task_type,
        )

    def hybrid_embed_batch(
        self,
        texts: List[str],
        dense_batch_size: int = 100,
        sparse_batch_size: int = 8,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> Dict[str, Any]:
        """`HybridClient.embed_batch` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.hybrid.embed_batch(
            texts=texts,
            dense_batch_size=dense_batch_size,
            sparse_batch_size=sparse_batch_size,
            task_type=task_type,
        )

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
        text_key: str = "text",
        method: str = "colbert",
        *,
        model: str = "gemini-3.1-flash-lite",
        max_docs: int = 50,
        timeout: float = 10.0,
        max_doc_length: int = 500,
    ) -> List[Dict[str, Any]]:
        """`RerankerClient.rerank` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.reranker.rerank(
            query=query,
            documents=documents,
            top_k=top_k,
            text_key=text_key,
            method=method,
            model=model,
            max_docs=max_docs,
            timeout=timeout,
            max_doc_length=max_doc_length,
        )

    def hybrid_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 20,
        use_rerank: bool = True,
        rerank_top_k: int = 50,
        payload_filter: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """`HybridClient.search` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`DenseClient.search` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`SparseClient.search` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.sparse.search(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
            score_threshold=score_threshold,
            payload_filter=payload_filter,
        )

    def create_collection(
        self,
        name: str,
        dense_dim: int = 1536,
        distance: str = "Cosine",
        recreate: bool = False,
        mode: str = "hybrid",
    ) -> Dict[str, str]:
        """`CollectionClient.create` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.create(
            name=name,
            dense_dim=dense_dim,
            distance=distance,
            recreate=recreate,
            mode=mode,
        )

    def get_collection_info(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """`CollectionClient.info` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.info(
            name=name,
        )

    def delete_collection(
        self,
        name: str,
    ) -> Dict[str, str]:
        """`CollectionClient.delete` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.delete(
            name=name,
        )

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
        """`CollectionClient.upsert_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.upsert_points(
            collection_name=collection_name,
            texts=texts,
            dense_vectors=dense_vectors,
            metadatas=metadatas,
            ids=ids,
            sparse_vectors=sparse_vectors,
            batch_size=batch_size,
        )

    def get_points(
        self,
        collection_name: str,
        ids: List[int | str],
        with_vectors: bool = False,
        with_payload: bool | List[str] = True,
    ) -> Dict[str, Any]:
        """`CollectionClient.get_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.get_points(
            collection_name=collection_name,
            ids=ids,
            with_vectors=with_vectors,
            with_payload=with_payload,
        )

    def delete_points(
        self,
        collection_name: str,
        ids: List[int | str],
    ) -> Dict[str, Any]:
        """`CollectionClient.delete_points` 바로가기. 설명은 원본 docstring 에 있다."""
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
        with_payload: bool | List[str] = True,
    ) -> Dict[str, Any]:
        """`CollectionClient.scroll_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.scroll_points(
            collection_name=collection_name,
            limit=limit,
            offset=offset,
            with_vectors=with_vectors,
            with_payload=with_payload,
        )

    def iter_points(
        self,
        collection_name: str,
        limit: int = 20,
        with_vectors: bool = False,
        with_payload: bool | List[str] = True,
    ) -> Iterator[Dict[str, Any]]:
        """`CollectionClient.iter_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.iter_points(
            collection_name=collection_name,
            limit=limit,
            with_vectors=with_vectors,
            with_payload=with_payload,
        )

    def count_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> int:
        """`CollectionClient.count_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.count_points(
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    def has_points(
        self,
        collection_name: str,
        payload_filter: Optional[dict] = None,
    ) -> bool:
        """`CollectionClient.has_points` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.has_points(
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    def set_payload(
        self,
        collection_name: str,
        ids: List[int | str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """`CollectionClient.set_payload` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`CollectionClient.overwrite_payload` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`CollectionClient.delete_payload_keys` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`CollectionClient.clear_payload` 바로가기. 설명은 원본 docstring 에 있다."""
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
        """`CollectionClient.create_payload_index` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.create_payload_index(
            collection_name=collection_name,
            field=field,
            schema=schema,
        )

    def list_payload_indexes(
        self,
        collection_name: str,
    ) -> Dict[str, str]:
        """`CollectionClient.list_payload_indexes` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.list_payload_indexes(
            collection_name=collection_name,
        )

    def delete_payload_index(
        self,
        collection_name: str,
        field: str,
    ) -> Dict[str, Any]:
        """`CollectionClient.delete_payload_index` 바로가기. 설명은 원본 docstring 에 있다."""
        return self.collection.delete_payload_index(
            collection_name=collection_name,
            field=field,
        )

    embed_text = embed
    sparse_encode = sparse_embed
    sparse_encode_batch = sparse_embed_batch

    # <<< 생성된 바로가기 끝

    def status(self) -> Dict[str, Any]:
        """서비스와 각 모델의 상태를 조회한다.

        `GET /v1/admin/status` 를 부른다. `vdb-handler`에는 모델 로드와 무관하게 뜨는
        가벼운 `/health` 가 따로 있는데 그것과는 다른 경로이고, 돌려주는 것도 많다.

        Returns:
            `{"dense": {...}, "sparse": {...}, "colbert": {...}, "qdrant": {...}}`.
            각 항목이 무엇을 담는지는 `vdb-handler`가 아직 계약으로 적지 않았다
            (`KiwoomAM/Kiwoom-Handler#83`).
        """
        return self._client.get("/v1/admin/status")

    # 옛 이름. 경고는 붙이지 않는다 — 바로가기에서 경고를 내면 stacklevel 이 SDK 내부를
    # 짚어 호출자가 자기 호출부를 찾지 못한다. 전에 `upsert` 가 그 우회 때문에 한 연산에
    # 이름이 셋이었고, 그 메서드를 지우면서 우회도 함께 사라졌다 (#114).
    health_check = status

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
