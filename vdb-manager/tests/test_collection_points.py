# -*- coding: utf-8 -*-
"""조건부 포인트 개수 조회와 포인트별 id 적재.

서버(KiwoomAM/Kiwoom-Handler#69)가 연 `/count` 와 `/upsert_points` 에 대응한다.
경로와 body 를 캡처해 검증하는 이 패키지의 관례를 따른다 — 특히 `/upsert_points` 에
`start_id` 가 실리면 서버가 422 를 내므로, "키가 빠졌는지" 를 직접 본다.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict

import pytest

from vdb_manager.collection import CollectionClient

COLLECTION = "fnguide_reports_hybrid"
UUID_A = "3f2b0c4e-1d5a-4c9b-8e7f-2a6b4c8d0e1f"
FILTER = {"must": [{"key": "source_pdf", "match": {"value": "a.pdf"}}]}


class FakeClient:
    """post/delete/get 호출의 경로와 body 를 기록하는 테스트 더블."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[Dict[str, Any]] = []
        self.response = response

    def _record(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.response

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._record("POST", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._record("DELETE", path, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._record("GET", path, **kwargs)


def _collection(response: Any = None) -> tuple[CollectionClient, FakeClient]:
    client = FakeClient(response)
    return CollectionClient(client), client  # type: ignore[arg-type]


# ------------------------------------------------------------------
# count_points
# ------------------------------------------------------------------


def test_count_points_hits_count_endpoint():
    col, client = _collection({"count": 3})

    result = col.count_points(COLLECTION, FILTER)

    assert result == 3
    assert client.calls[0]["path"] == f"/v1/collections/{COLLECTION}/count"
    assert client.calls[0]["json"] == {"payload_filter": FILTER}


def test_count_points_without_filter_counts_whole_collection():
    col, client = _collection({"count": 24407})

    assert col.count_points(COLLECTION) == 24407
    assert client.calls[0]["json"] == {"payload_filter": None}


def test_count_points_never_sends_exact():
    """서버는 `exact` 를 422 로 거부한다 — 근사치로 셀 길을 열지 않기 때문이다."""
    col, client = _collection({"count": 0})

    col.count_points(COLLECTION, FILTER)

    assert "exact" not in client.calls[0]["json"]


# ------------------------------------------------------------------
# has_points
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "count, expected", [(0, False), (1, True), (24407, True)]
)
def test_has_points_derives_from_count(count, expected):
    col, client = _collection({"count": count})

    assert col.has_points(COLLECTION, FILTER) is expected
    assert client.calls[0]["path"] == f"/v1/collections/{COLLECTION}/count"


def test_has_points_forwards_filter_unchanged():
    col, client = _collection({"count": 1})

    col.has_points(COLLECTION, FILTER)

    assert client.calls[0]["json"] == {"payload_filter": FILTER}


# ------------------------------------------------------------------
# upsert_points
# ------------------------------------------------------------------


def _upsert_kwargs(**overrides) -> Dict[str, Any]:
    kwargs = {
        "texts": ["첫 문서", "둘째 문서"],
        "dense_vectors": [[0.1, 0.2], [0.3, 0.4]],
        "metadatas": [{"source_pdf": "a.pdf"}, {"source_pdf": "b.pdf"}],
        "ids": [1001, UUID_A],
    }
    kwargs.update(overrides)
    return kwargs


def test_upsert_points_hits_new_endpoint_with_ids():
    col, client = _collection({"status": "upserted"})

    col.upsert_points(COLLECTION, **_upsert_kwargs())

    call = client.calls[0]
    assert call["path"] == f"/v1/collections/{COLLECTION}/upsert_points"
    assert call["json"]["ids"] == [1001, UUID_A]


def test_upsert_points_never_sends_start_id():
    """서버의 `UpsertPointsRequest` 는 extra="forbid" 라 `start_id` 가 오면 422 다."""
    col, client = _collection({"status": "upserted"})

    col.upsert_points(COLLECTION, **_upsert_kwargs())

    assert "start_id" not in client.calls[0]["json"]


def test_upsert_points_omits_sparse_vectors_when_absent():
    col, client = _collection({"status": "upserted"})

    col.upsert_points(COLLECTION, **_upsert_kwargs())

    assert "sparse_vectors" not in client.calls[0]["json"]


def test_upsert_points_sends_sparse_vectors_as_lists():
    col, client = _collection({"status": "upserted"})

    col.upsert_points(
        COLLECTION, **_upsert_kwargs(sparse_vectors=[([1, 5], [0.7, 0.3]), ([2], [0.9])])
    )

    assert client.calls[0]["json"]["sparse_vectors"] == [
        [[1, 5], [0.7, 0.3]],
        [[2], [0.9]],
    ]


# ------------------------------------------------------------------
# upsert 는 legacy
# ------------------------------------------------------------------


def test_upsert_warns_and_points_at_replacement():
    col, client = _collection({"status": "upserted"})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        col.upsert(
            COLLECTION,
            texts=["a"],
            dense_vectors=[[0.1]],
            metadatas=[{}],
            start_id=100,
        )

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "DeprecationWarning 이 나야 한다"
    assert "upsert_points" in " ".join(str(w.message) for w in deprecations)


def test_upsert_keeps_working_while_deprecated():
    """경고만 더한다 — 동작을 바꾸면 소비자 전환 전에 적재가 멈춘다."""
    col, client = _collection({"status": "upserted"})

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        col.upsert(
            COLLECTION, texts=["a"], dense_vectors=[[0.1]], metadatas=[{}], start_id=100
        )

    call = client.calls[0]
    assert call["path"] == f"/v1/collections/{COLLECTION}/upsert"
    assert call["json"]["start_id"] == 100


# ------------------------------------------------------------------
# id 폭 — 적재와 조회·삭제·순회가 어긋나면 손댈 수 없는 포인트가 생긴다
# ------------------------------------------------------------------


def test_get_points_forwards_uuid_ids():
    col, client = _collection({"points": []})

    col.get_points(COLLECTION, ids=[UUID_A, 1001])

    assert client.calls[0]["json"]["ids"] == [UUID_A, 1001]


def test_delete_points_forwards_uuid_ids():
    col, client = _collection({"status": "deleted"})

    col.delete_points(COLLECTION, ids=[UUID_A])

    assert client.calls[0]["json"]["ids"] == [UUID_A]


def test_scroll_points_forwards_uuid_offset():
    """Qdrant 의 scroll offset 은 point id 라, UUID 컬렉션에서는 문자열이다."""
    col, client = _collection({"points": [], "next_offset": None})

    col.scroll_points(COLLECTION, limit=2, offset=UUID_A)

    assert client.calls[0]["json"]["offset"] == UUID_A
