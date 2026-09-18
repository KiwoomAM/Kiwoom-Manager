# -*- coding: utf-8 -*-
"""컬렉션 관리 / 포인트 CRUD SDK 클라이언트"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Tuple

from vdb_manager._client import SyncClient


def _reject_if_empty(name: str, value: Any, reason: str) -> None:
    """빈 인자를 서버에 보내기 전에 막는다.

    서버도 422 로 거부하지만 층이 다른 두 방어다. 서버의 것은 모든 소비자(다른
    언어·curl·다음에 생길 SDK)를 향하고, 이것은 이 SDK 사용자에게 왕복 전에
    알린다.

    빈 것만 막고 형식은 판정하지 않는다. u64 범위나 UUID 모양까지 흉내 내면 서버가
    규칙을 넓힐 때마다 SDK 가 정당한 요청을 막는다.

    Args:
        name: 인자 이름. 메시지에 그대로 실린다.
        value: 검사할 값.
        reason: 왜 막는지. 호출자마다 다르다.

    Raises:
        ValueError: `value` 가 비었을 때.
    """
    if not value:
        raise ValueError(f"{name} 가 빔 — {reason}")


def _warn_if_index_cannot_be_deleted(field: str) -> None:
    """지울 수 없는 색인을 만들려 할 때 그 자리에서 알린다.

    되돌릴 수 없게 만드는 행위는 만드는 쪽이다. 지우는 시점에 알려 주면 이미
    늦다. 막지는 않는다 — 슬래시 든 필드에 색인을 거는 것은 정당한 요구이고,
    걸면 조회가 실제로 빨라진다. 못 하는 것은 지우는 일뿐이다.

    KiwoomAM/Kiwoom-Handler#74 가 고쳐지면 함께 걷어낼 임시 조치다.

    Args:
        field: 색인을 걸 payload 필드명.
    """
    if "/" not in field:
        return
    warnings.warn(
        f"필드명 '{field}' 에 슬래시 있음 — 색인을 만들 수는 있으나 지울 수 없음"
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
                None 이거나 생략하면 컬렉션 전체 개수. 지원하는 절과 조건의 정의는
                서버의 `vdb-handler/app/core/vector_store.py::_build_qdrant_filter` 가
                단일 출처다. 모르는 절을 보내면 서버가 422 로 거부한다.

        Returns:
            조건에 맞는 포인트 수.

        Raises:
            ValueError: payload_filter 가 빈 dict 일 때. 서버는 그것을 필터 없음으로
                보아 전체를 세는데, 조건을 만들다 비어서 나온 경우 그 결과가
                "이미 적재됨" 으로 읽혀 적재가 누락된다.
        """
        if payload_filter is not None and not payload_filter:
            raise ValueError(
                "payload_filter 가 빈 dict — 빈 조건은 서버에서 필터 없음이 되어 "
                "컬렉션 전체를 셈, 전체 개수를 원하면 인자를 생략"
            )

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
            `{"status": "updated", "collection": ..., "count": ...}`.
            `count` 는 영향받은 수가 아니라 요청한 id 수다. Qdrant 가 실제 변경
            건수를 돌려주지 않아 세는 것이 불가능하다.

        Raises:
            ValueError: `ids` 나 `payload` 가 비었을 때.

        Note:
            큰 id 목록은 호출자가 끊어 보낸다. 한 요청에 다 실으면 Qdrant 가
            처리하는 동안 HTTP 요청 하나가 열려 있고, 타임아웃이 나면 예외는
            실패라고 말하는데 서버 작업은 되돌려지지 않아 일부가 적용된 상태로
            남는다. 어디까지 갔는지는 호출자의 루프만 알 수 있다.
        """
        _reject_if_empty("ids", ids, "대상 포인트 없음")
        _reject_if_empty("payload", payload, "바꿀 내용 없음, 비우려면 clear_payload 사용")
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

        스키마를 전면 개편할 때 남은 키를 하나씩 지우지 않아도 되게 하는 경로다.
        주지 않은 키를 남기려면 `set_payload` 를 쓴다.

        어떤 키도 보호하지 않는다 — 소비자가 존재 확인에 쓰는 정체성 키도 함께
        사라진다. 그 결정의 근거는 KiwoomAM/Kiwoom-Handler 의 ADR 0002 에 있다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id. `set_payload` 와 같다.
            payload: 새 payload 전체.

        Returns:
            `{"status": "replaced", "collection": ..., "count": ...}`.
            `count` 의 의미는 `set_payload` 와 같다.

        Raises:
            ValueError: `ids` 나 `payload` 가 비었을 때.
        """
        _reject_if_empty("ids", ids, "대상 포인트 없음")
        _reject_if_empty("payload", payload, "바꿀 내용 없음, 비우려면 clear_payload 사용")
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

        폐기된 필드를 컬렉션에서 걷어내는 정리 작업의 자리다. payload 를 통째로
        비우려면 `clear_payload` 를 쓴다 — `keys` 를 생략해서 도달할 수 있는
        연산이 아니다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id.
            keys: 지울 payload 키. 필수다.

        Returns:
            `{"status": ..., "collection": ..., "count": ..., "keys": [...]}`.
            무엇을 지웠는지가 응답에 실린다 — 되돌릴 수단이 없는 연산이라
            호출자가 로그로 남길 수 있어야 한다.

        Raises:
            ValueError: `ids` 나 `keys` 가 비었을 때.
        """
        _reject_if_empty("ids", ids, "대상 포인트 없음")
        _reject_if_empty("keys", keys, "지울 키 없음, 전부 비우려면 clear_payload 사용")
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

        잘못 채워진 포인트를 초기화하는 경로다. 벡터는 남고 payload 만 사라진다.

        `keys` 를 받지 않는다. 받으면 키 삭제와 구분이 흐려지고, 인자를 빠뜨렸다는
        이유로 전체가 지워지는 길이 열린다.

        Args:
            collection_name: 대상 컬렉션.
            ids: 대상 포인트 id.

        Returns:
            `{"status": "cleared", "collection": ..., "count": ...}`.

        Raises:
            ValueError: `ids` 가 비었을 때.
        """
        _reject_if_empty("ids", ids, "대상 포인트 없음")
        return self._client.delete(
            f"/v1/collections/{collection_name}/points/payload/all",
            json={"ids": ids},
        )

    # ------------------------------------------------------------------
    # payload 색인
    # ------------------------------------------------------------------

    def create_payload_index(
        self,
        collection_name: str,
        field: str,
        schema: str,
    ) -> Dict[str, Any]:
        """payload 필드에 색인을 만든다.

        조건 조회(`count_points`·`has_points`·검색의 `payload_filter`)에 쓰는
        필드에 색인이 없으면 Qdrant 가 전수 주사하므로, 비용이 컬렉션 크기와 함께
        늘어난다. 색인을 걸면 그 비용을 크기에서 떼어 낼 수 있다.

        Args:
            collection_name: 대상 컬렉션.
            field: 색인을 걸 payload 필드명.
            schema: 색인 종류. `keyword`·`integer`·`float`·`geo`·`text`·`bool`·
                `datetime`·`uuid` 를 서버가 받으며 그 밖은 422 로 거부한다.
                기본값을 두지 않는 이유는 틀린 선택이 조용하기 때문이다 — 오타는
                서버가 잡지만, 날짜 필드에 `keyword` 를 거는 것은 유효한 요청이라
                성공하고 range 질의만 색인을 타지 못한 채 느려진다.

        Returns:
            `{"status": ..., "collection": ..., "field": ...}`.

        Raises:
            ValueError: `field` 가 비었을 때.

        Warns:
            UserWarning: `field` 에 슬래시가 있을 때. 만들 수는 있으나 지울 수
                없는 색인이 된다. 아래 Note 참고.

        Note:
            이름에 슬래시가 든 필드는 색인을 지울 수 없다. 원인은 벤더 클라이언트가
            필드명을 URL 경로에 percent-encoding 없이 넣는 것이며 Qdrant 자체는
            인코딩된 경로를 받아들인다. KiwoomAM/Kiwoom-Handler#74 에 실측이 있다.
            같은 원리로 URL 경로에서 의미를 갖는 다른 문자도 깨질 수 있으나, 실제로
            측정한 것은 슬래시 하나라 그것만 경고한다.
        """
        _reject_if_empty("field", field, "대상 필드 없음")
        _warn_if_index_cannot_be_deleted(field)
        return self._client.post(
            f"/v1/collections/{collection_name}/indexes",
            json={"field": field, "schema": schema},
        )

    def list_payload_indexes(self, collection_name: str) -> Dict[str, str]:
        """어떤 필드에 어떤 색인이 걸려 있는지 본다.

        배포 전에 색인이 실제로 걸렸는지 확인하는 자리다. 색인이 하나도 없는 것은
        오류가 아니라 흔한 상태다.

        Args:
            collection_name: 대상 컬렉션.

        Returns:
            필드명에서 색인 종류로 가는 매핑. 예: `{"source_pdf": "keyword"}`.
            색인이 없으면 빈 dict.
        """
        resp = self._client.get(f"/v1/collections/{collection_name}/indexes")
        return resp["indexes"]

    def delete_payload_index(
        self,
        collection_name: str,
        field: str,
    ) -> Dict[str, Any]:
        """payload 색인을 지운다. payload 값은 그대로 남는다.

        잘못 만든 색인이 메모리를 영구히 먹지 않게 하는 경로다.

        Args:
            collection_name: 대상 컬렉션.
            field: 색인을 지울 payload 필드명.

        Returns:
            `{"status": ..., "collection": ..., "field": ...}`.

        Raises:
            ValueError: `field` 가 비었을 때.

        Note:
            이름에 슬래시가 든 필드는 색인이 있어도 404 가 돌아온다. 필드명을 잘못
            적어서가 아니라 벤더 클라이언트의 한계이며
            KiwoomAM/Kiwoom-Handler#74 에 실측과 대안이 있다.
        """
        _reject_if_empty("field", field, "대상 필드 없음")
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
