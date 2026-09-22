# -*- coding: utf-8 -*-
"""컬렉션 관리 / 포인트 CRUD SDK 클라이언트"""

from __future__ import annotations

import warnings
from typing import Any, Dict, Iterator, List, Optional, Tuple

from vdb_manager._client import SyncClient
from vdb_manager._guards import (
    reject_empty_filter,
    reject_empty_selection,
    reject_if_empty,
)


_EMPTY_PAYLOAD_REASON = "바꿀 내용 없음, 비우려면 clear_payload 사용"


def _warn_if_index_cannot_be_deleted(field: str) -> None:
    """지울 수 없는 index 를 만들려 할 때 그 자리에서 알린다.

    되돌릴 수 없게 만드는 행위는 만드는 쪽이다. 지우는 시점에 알려 주면 이미
    늦다. 막지는 않는다 — 슬래시 든 필드에 index 를 거는 것은 정당한 요구이고,
    걸면 조회가 실제로 빨라진다. 못 하는 것은 지우는 일뿐이다.

    KiwoomAM/Kiwoom-Handler#74 가 고쳐지면 함께 걷어낼 임시 조치다.

    Args:
        field: index 를 걸 payload 필드명.
    """
    if "/" not in field:
        return
    warnings.warn(
        f"필드명 '{field}' 에 슬래시 있음 — index 를 만들 수는 있으나 지울 수 없음"
        " (벤더 클라이언트 한계, KiwoomAM/Kiwoom-Handler#74)."
        " 지울 수 있어야 하면 슬래시 없는 필드명 사용",
        UserWarning,
        stacklevel=3,
    )


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
        return self._upsert_legacy(
            collection_name,
            texts=texts,
            dense_vectors=dense_vectors,
            metadatas=metadatas,
            sparse_vectors=sparse_vectors,
            start_id=start_id,
            batch_size=batch_size,
        )

    def _upsert_legacy(
        self,
        collection_name: str,
        texts: List[str],
        dense_vectors: List[List[float]],
        metadatas: List[Dict[str, Any]],
        sparse_vectors: Optional[List[Tuple[List[int], List[float]]]] = None,
        start_id: int = 0,
        batch_size: int = 100,
    ) -> Dict[str, Any]:
        """경고 없이 legacy 경로로 보낸다.

        `upsert` 와 `VDBManager.upsert` 가 각자 자기 호출자를 가리키는 경고를 낸 뒤
        여기로 모인다. 구현을 여기 두지 않으면 바로가기로 부른 소비자의 경고가 SDK
        내부 줄을 짚어, 정작 고쳐야 할 자기 코드를 찾지 못한다.
        """
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
        다만 유도한 id 의 폭을 줄이지 않아야 한다. 작은 범위로 접으면(`% 1_000_000`
        등) 기존 순번 id 구간을 때려 남의 포인트를 덮어쓴다.

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
        with_payload: bool | List[str] = True,
    ) -> Dict[str, Any]:
        """id 로 포인트를 조회한다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 조회할 포인트 id 목록.
            with_vectors: 벡터를 함께 받을지 여부.
            with_payload: True 면 payload 전체, False 면 받지 않음, 항목명 목록이면
                그 항목만. 중첩 경로(`a.b`)와 감싼 키(`'"종목/분류명"'`)도 쓸 수 있다.
                모르는 항목명은 `vdb-handler`가 막지 않고 빈 payload 로 돌려준다.

        Returns:
            `{"points": [...], "count": N}`. 없는 id 는 조용히 빠지므로 count 가
            요청한 개수보다 작을 수 있다 — 어느 id 가 빠졌는지는 돌려받은 포인트의
            id 로 가려낸다.

        Raises:
            ValueError: with_payload 가 빈 목록일 때.
        """
        reject_empty_selection(with_payload)
        return self._client.post(
            f"/v1/collections/{collection_name}/points",
            json={"ids": ids, "with_vectors": with_vectors, "with_payload": with_payload},
        )

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
        with_payload: bool | List[str] = True,
    ) -> Dict[str, Any]:
        """포인트를 페이지 단위로 조회한다.

        전수 순회에는 `iter_points` 가 있다. 이어받기와 종료 판정을 직접 다뤄야 할
        때만 이 메서드를 쓴다.

        Args:
            collection_name: 대상 컬렉션.
            limit: 한 페이지에 받을 포인트 수.
            offset: 이어받기 키. 첫 페이지는 None.
            with_vectors: 벡터를 함께 받을지 여부.
            with_payload: True 면 payload 전체, False 면 받지 않음, 항목명 목록이면
                그 항목만. 순회 전송량이 문제라면 여기서 줄인다.

        Returns:
            `{"points": [...], "next_offset": ...}`. 다음 페이지는 next_offset 을
            offset 으로 넘겨 받는다. 순회의 끝은 next_offset 이 None 인 것으로
            판정한다 — 0 은 실재하는 포인트 id 라 falsy 로 보면 첫 페이지에서 멈춘다.

        Raises:
            ValueError: with_payload 가 빈 목록일 때.
        """
        reject_empty_selection(with_payload)
        return self._client.post(
            f"/v1/collections/{collection_name}/scroll",
            json={
                "limit": limit,
                "offset": offset,
                "with_vectors": with_vectors,
                "with_payload": with_payload,
            },
        )

    def iter_points(
        self,
        collection_name: str,
        limit: int = 20,
        with_vectors: bool = False,
        with_payload: bool | List[str] = True,
    ) -> Iterator[Dict[str, Any]]:
        """컬렉션의 포인트를 처음부터 끝까지 하나씩 돌려준다.

        `scroll_points` 를 페이지마다 부르며 이어받기 키를 넘긴다. 호출자가 종료
        조건을 직접 다루지 않아도 되는 것이 이 메서드의 존재 이유다 — 이어받기 키
        0 은 실재하는 포인트 id 라, falsy 로 끝을 판정하면 첫 페이지에서 멈춘다.

        Args:
            collection_name: 대상 컬렉션.
            limit: 한 번에 받아 올 페이지 크기. 돌려주는 단위는 포인트 하나다.
            with_vectors: 벡터를 함께 받을지 여부.
            with_payload: True 면 payload 전체, False 면 받지 않음, 항목명 목록이면
                그 항목만. 전수 순회는 이 인자의 효과가 가장 큰 자리다.

        Returns:
            포인트를 하나씩 돌려주는 iterator. 페이지 경계는 드러나지 않는다.

        Raises:
            ValueError: with_payload 가 빈 목록일 때. 순회를 시작하기 전에 터진다.
            RuntimeError: `vdb-handler`가 같은 이어받기 키를 두 번 줄 때. 그대로 두면 순회가
                끝나지 않아 배치가 조용히 멈춰 선다. 이것은 순회 도중에 터진다.
        """
        # 가드를 제너레이터 밖에 둔다. 안에 두면 첫 `next()` 까지 실행이 미뤄져
        # 호출 자리에서는 아무 일도 일어나지 않는다 — 왕복 전에 알린다는 가드의
        # 존재 이유가 이 메서드에서만 성립하지 않게 된다.
        reject_empty_selection(with_payload)
        return self._iter_pages(collection_name, limit, with_vectors, with_payload)

    def _iter_pages(
        self,
        collection_name: str,
        limit: int,
        with_vectors: bool,
        with_payload: bool | List[str],
    ) -> Iterator[Dict[str, Any]]:
        """`iter_points` 의 제너레이터 본체. 인자 검사는 호출 쪽에서 이미 끝났다."""
        offset: Optional[int | str] = None
        while True:
            page = self.scroll_points(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                with_vectors=with_vectors,
                with_payload=with_payload,
            )
            yield from page.get("points") or []

            next_offset = page.get("next_offset")
            if next_offset is None:
                return
            if next_offset == offset:
                raise RuntimeError(
                    f"이어받기 키가 나아가지 않음 — 같은 자리 {next_offset!r} 를 두 번 받음, "
                    "그대로 두면 순회가 끝나지 않으므로 멈춤"
                )
            offset = next_offset

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
                None 이거나 생략하면 컬렉션 전체 개수. 지원하는 절과 조건의 정의는
                서버의 `vdb-handler/app/core/vector_store.py::_build_qdrant_filter` 가
                단일 출처다. 모르는 절을 보내면 서버가 422 로 거부한다.

        Returns:
            조건에 맞는 포인트 수.

        Raises:
            ValueError: payload_filter 가 빈 dict 일 때. 왜 막는지는
                `_guards.reject_empty_filter` 가 단일 출처다.
        """
        reject_empty_filter(payload_filter)

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

        Raises:
            ValueError: payload_filter 가 빈 dict 일 때 (`count_points` 와 같다).
        """
        return self.count_points(collection_name, payload_filter) > 0

    # ------------------------------------------------------------------
    # payload 부분 수정
    # ------------------------------------------------------------------

    def set_payload(
        self,
        collection_name: str,
        ids: List[int | str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """포인트 payload 에 주는 키만 더하거나 덮는다. 나머지 키는 보존된다.

        주는 id 중 없는 것은 고르지 않는다. 서버가 그렇게 보내기 때문이며, 예전에는
        Qdrant 가 404 를 내면서 있는 포인트의 변경은 이미 끝내 놓아 호출자가 아무
        일도 없었다고 오해했다. 무엇이 살아 있었는지 알아야 하면 `get_points` 로
        읽는다 — 존재 판정은 쓰기가 아니라 읽기의 일이다.

        벡터는 건드리지 않으므로 필드 하나를 고치자고 임베딩을 다시 돌릴 필요가
        없다. 스키마가 바뀌어 기존 포인트를 채워 넣는 백필이 이 메서드의 자리다.

        payload 를 통째로 바꾸려면 `overwrite_payload` 를 쓴다. 둘은 같은 경로로
        가고 HTTP 메서드만 다르므로, 잘못 고르면 주지 않은 키가 조용히 사라진다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id. 부호 없는 정수 또는 UUID 만 허용되며 형식 검증은
                서버가 한다. 수천 개를 한 번에 보내지 말 것 — 아래 Note 참고.
            payload: 더하거나 덮을 key-value.

        Returns:
            `{"status": "updated", "collection": ..., "requested": ...}`.
            `requested` 는 요청한 id 수다. Qdrant 가 실제 변경 건수를 돌려주지 않아
            셀 수가 없으므로 이름으로 그 사실을 드러낸다 — `count_points` 가
            돌려주는 `count` 는 진짜로 센 값이라 서로 다른 것이다.

        Raises:
            ValueError: `ids` 나 `payload` 가 비었을 때.

        Note:
            큰 id 목록은 호출자가 끊어 보낸다. 한 요청에 다 실으면 Qdrant 가
            처리하는 동안 HTTP 요청 하나가 열려 있고, 타임아웃이 나면 예외는
            실패라고 말하는데 서버 작업은 되돌려지지 않아 일부가 적용된 상태로
            남는다. 어디까지 갔는지는 호출자의 루프만 알 수 있다.
        """
        reject_if_empty("ids", ids, "대상 포인트 없음")
        reject_if_empty("payload", payload, _EMPTY_PAYLOAD_REASON)
        return self._client.patch(
            f"/v1/collections/{collection_name}/points/payload",
            json={"ids": ids, "payload": payload},
        )

    def overwrite_payload(
        self,
        collection_name: str,
        ids: List[int | str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """포인트 payload 를 통째로 주는 것으로 바꾼다. 기존 키는 전부 사라진다.

        주는 id 중 없는 것은 고르지 않는다. 서버가 그렇게 보내기 때문이며, 예전에는
        Qdrant 가 404 를 내면서 있는 포인트의 변경은 이미 끝내 놓아 호출자가 아무
        일도 없었다고 오해했다. 무엇이 살아 있었는지 알아야 하면 `get_points` 로
        읽는다 — 존재 판정은 쓰기가 아니라 읽기의 일이다.

        스키마를 전면 개편할 때 남은 키를 하나씩 지우지 않아도 되게 하는 경로다.
        주지 않은 키를 남기려면 `set_payload` 를 쓴다.

        어떤 키도 보호하지 않는다 — 소비자가 존재 확인에 쓰는 정체성 키도 함께
        사라진다. 그 결정의 근거는 KiwoomAM/Kiwoom-Handler 의 ADR 0002 에 있다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id. `set_payload` 와 같다.
            payload: 새 payload 전체.

        Returns:
            `{"status": "replaced", "collection": ..., "requested": ...}`.
            `requested` 의 의미는 `set_payload` 와 같다.

        Raises:
            ValueError: `ids` 나 `payload` 가 비었을 때.
        """
        reject_if_empty("ids", ids, "대상 포인트 없음")
        reject_if_empty("payload", payload, _EMPTY_PAYLOAD_REASON)
        return self._client.put(
            f"/v1/collections/{collection_name}/points/payload",
            json={"ids": ids, "payload": payload},
        )

    def delete_payload_keys(
        self,
        collection_name: str,
        ids: List[int | str],
        keys: List[str],
    ) -> Dict[str, Any]:
        """포인트 payload 에서 주는 키만 지운다.

        주는 id 중 없는 것은 고르지 않는다. 서버가 그렇게 보내기 때문이며, 예전에는
        Qdrant 가 404 를 내면서 있는 포인트의 변경은 이미 끝내 놓아 호출자가 아무
        일도 없었다고 오해했다. 무엇이 살아 있었는지 알아야 하면 `get_points` 로
        읽는다 — 존재 판정은 쓰기가 아니라 읽기의 일이다.

        폐기된 필드를 컬렉션에서 걷어내는 정리 작업의 자리다. payload 를 통째로
        비우려면 `clear_payload` 를 쓴다 — `keys` 를 생략해서 도달할 수 있는
        연산이 아니다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id.
            keys: 지울 payload 키. 필수다.

        Returns:
            `{"status": ..., "collection": ..., "requested": ..., "ids": [...],
            "keys": [...]}`. 무엇을 어디서 지웠는지가 응답에 실린다 — 되돌릴
            수단이 없는 연산이라 호출자가 로그로 남길 수 있어야 한다.

        Raises:
            ValueError: `ids` 나 `keys` 가 비었을 때.
        """
        reject_if_empty("ids", ids, "대상 포인트 없음")
        reject_if_empty("keys", keys, "지울 키 없음, 전부 비우려면 clear_payload 사용")
        return self._client.delete(
            f"/v1/collections/{collection_name}/points/payload",
            json={"ids": ids, "keys": keys},
        )

    def clear_payload(
        self,
        collection_name: str,
        ids: List[int | str],
    ) -> Dict[str, Any]:
        """포인트 payload 를 통째로 비운다.

        주는 id 중 없는 것은 고르지 않는다. 서버가 그렇게 보내기 때문이며, 예전에는
        Qdrant 가 404 를 내면서 있는 포인트의 변경은 이미 끝내 놓아 호출자가 아무
        일도 없었다고 오해했다. 무엇이 살아 있었는지 알아야 하면 `get_points` 로
        읽는다 — 존재 판정은 쓰기가 아니라 읽기의 일이다.

        잘못 채워진 포인트를 초기화하는 경로다. 벡터는 남고 payload 만 사라진다.

        `keys` 를 받지 않는다. 받으면 키 삭제와 구분이 흐려지고, 인자를 빠뜨렸다는
        이유로 전체가 지워지는 길이 열린다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id.

        Returns:
            `{"status": "cleared", "collection": ..., "requested": ...,
            "ids": [...]}`.

        Raises:
            ValueError: `ids` 가 비었을 때.
        """
        reject_if_empty("ids", ids, "대상 포인트 없음")
        return self._client.delete(
            f"/v1/collections/{collection_name}/points/payload/all",
            json={"ids": ids},
        )

    # ------------------------------------------------------------------
    # payload index
    # ------------------------------------------------------------------

    def create_payload_index(
        self,
        collection_name: str,
        field: str,
        schema: str,
    ) -> Dict[str, Any]:
        """payload 필드에 index 를 만든다.

        조건 조회(`count_points`·`has_points`·검색의 `payload_filter`)에 쓰는
        필드에 index 가 없으면 Qdrant 가 전수 스캔하므로, 비용이 컬렉션 크기와 함께
        늘어난다. index 를 걸면 그 비용을 크기에서 떼어 낼 수 있다.

        Args:
            collection_name: 대상 컬렉션.
            field: index 를 걸 payload 필드명.
            schema: index 종류. 받는 값의 정의는 서버의
                `vdb-handler/app/models/requests.py::PayloadIndexSchema` 가 단일
                출처이며 그 밖은 422 로 거부한다. 기본값을 두지 않는 이유는 틀린
                선택이 조용하기 때문이다 — 오타는 서버가 잡지만, 날짜 필드에
                `keyword` 를 거는 것은 유효한 요청이라 성공하고 range 질의만 index 를
                타지 못한 채 느려진다.

        Returns:
            `{"status": ..., "collection": ..., "field": ..., "schema": ...}`.

        Raises:
            ValueError: `field` 나 `schema` 가 비었을 때.

        Warns:
            UserWarning: `field` 에 슬래시가 있을 때. 아래 Note 참고.

        Note:
            이름에 슬래시가 든 필드는 index 를 지울 수 없다. 원인은 벤더 클라이언트가
            필드명을 URL 경로에 percent-encoding 없이 넣는 것이며 Qdrant 자체는
            인코딩된 경로를 받아들인다. KiwoomAM/Kiwoom-Handler#74 에 실측이 있다.
            같은 원리로 URL 경로에서 의미를 갖는 다른 문자도 깨질 수 있으나, 실제로
            측정한 것은 슬래시 하나라 그것만 경고한다.
        """
        reject_if_empty("field", field, "대상 필드 없음")
        reject_if_empty("schema", schema, "index 종류 없음")
        _warn_if_index_cannot_be_deleted(field)
        return self._client.post(
            f"/v1/collections/{collection_name}/indexes",
            json={"field": field, "schema": schema},
        )

    def list_payload_indexes(self, collection_name: str) -> Dict[str, str]:
        """어떤 필드에 어떤 index 가 걸려 있는지 본다.

        배포 전에 index 가 실제로 걸렸는지 확인하는 자리다. index 가 하나도 없는 것은
        오류가 아니라 흔한 상태다.

        Args:
            collection_name: 대상 컬렉션.

        Returns:
            필드명에서 index 종류로 가는 매핑. 예: `{"source_pdf": "keyword"}`.
            index 가 없으면 빈 dict.
        """
        resp = self._client.get(f"/v1/collections/{collection_name}/indexes")
        return resp["indexes"]

    def delete_payload_index(
        self,
        collection_name: str,
        field: str,
    ) -> Dict[str, Any]:
        """payload index 를 지운다. payload 값은 그대로 남는다.

        잘못 만든 index 가 메모리를 영구히 먹지 않게 하는 경로다.

        Args:
            collection_name: 대상 컬렉션.
            field: index 를 지울 payload 필드명.

        Returns:
            `{"status": ..., "collection": ..., "field": ...}`.

        Raises:
            ValueError: `field` 가 비었을 때.

        Note:
            이름에 슬래시가 든 필드는 index 가 있어도 404 가 돌아온다. 필드명을 잘못
            적어서가 아니라 벤더 클라이언트의 한계이며
            KiwoomAM/Kiwoom-Handler#74 에 실측과 대안이 있다.
        """
        reject_if_empty("field", field, "대상 필드 없음")
        return self._client.delete(
            f"/v1/collections/{collection_name}/indexes",
            json={"field": field},
        )

    @property
    def is_ready(self) -> bool:
        """Qdrant 연결 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["qdrant"].get("status") == "connected"
        except Exception:
            return False
