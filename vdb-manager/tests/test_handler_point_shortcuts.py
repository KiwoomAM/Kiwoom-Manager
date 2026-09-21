# -*- coding: utf-8 -*-
"""handler 바로가기가 collection 의 신규 메서드를 그대로 전달하는지 본다.

이 패키지는 collection 의 모든 메서드를 handler 에 미러링하는 관례를 지켜 왔다.
새 메서드만 빠지면 소비자가 `handler.count_points(...)` 를 기대했다 헤맨다.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

COLLECTION = "fnguide_reports_hybrid"
FILTER = {"must": [{"key": "source_pdf", "match": {"value": "a.pdf"}}]}


class FakeCollection:
    def __init__(self, result: Any) -> None:
        self.captured: Dict[str, Any] = {}
        self.result = result

    def _capture(self, name: str, **kwargs: Any) -> Any:
        self.captured = {"method": name, **kwargs}
        return self.result

    def count_points(self, collection_name, payload_filter=None):
        return self._capture(
            "count_points",
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    def has_points(self, collection_name, payload_filter=None):
        return self._capture(
            "has_points",
            collection_name=collection_name,
            payload_filter=payload_filter,
        )

    def upsert_points(self, collection_name, **kwargs):
        return self._capture("upsert_points", collection_name=collection_name, **kwargs)

    def set_payload(self, collection_name, **kwargs):
        return self._capture("set_payload", collection_name=collection_name, **kwargs)

    def overwrite_payload(self, collection_name, **kwargs):
        return self._capture(
            "overwrite_payload", collection_name=collection_name, **kwargs
        )

    def delete_payload_keys(self, collection_name, **kwargs):
        return self._capture(
            "delete_payload_keys", collection_name=collection_name, **kwargs
        )

    def clear_payload(self, collection_name, **kwargs):
        return self._capture("clear_payload", collection_name=collection_name, **kwargs)

    def create_payload_index(self, collection_name, **kwargs):
        return self._capture(
            "create_payload_index", collection_name=collection_name, **kwargs
        )

    def list_payload_indexes(self, collection_name, **kwargs):
        return self._capture(
            "list_payload_indexes", collection_name=collection_name, **kwargs
        )

    def delete_payload_index(self, collection_name, **kwargs):
        return self._capture(
            "delete_payload_index", collection_name=collection_name, **kwargs
        )


def _handler(result: Any):
    from vdb_manager import VDBManager

    handler = VDBManager()
    fake = FakeCollection(result)
    handler.collection = fake  # type: ignore[assignment]
    return handler, fake


def test_handler_count_points_forwards():
    handler, fake = _handler(3)

    assert handler.count_points(COLLECTION, payload_filter=FILTER) == 3
    assert fake.captured == {
        "method": "count_points",
        "collection_name": COLLECTION,
        "payload_filter": FILTER,
    }


def test_handler_has_points_forwards():
    handler, fake = _handler(True)

    assert handler.has_points(COLLECTION, payload_filter=FILTER) is True
    assert fake.captured["method"] == "has_points"
    assert fake.captured["payload_filter"] == FILTER


def test_handler_upsert_points_forwards_ids():
    handler, fake = _handler({"status": "upserted"})

    handler.upsert_points(
        COLLECTION,
        texts=["a"],
        dense_vectors=[[0.1]],
        metadatas=[{}],
        ids=[1001],
    )

    assert fake.captured["method"] == "upsert_points"
    assert fake.captured["ids"] == [1001]


def test_handler_has_no_start_id_shortcut_for_new_path():
    """바로가기가 legacy 인자를 새 경로로 흘리면 서버가 422 를 낸다."""
    handler, fake = _handler({"status": "upserted"})

    with pytest.raises(TypeError):
        handler.upsert_points(  # type: ignore[call-arg]
            COLLECTION,
            texts=["a"],
            dense_vectors=[[0.1]],
            metadatas=[{}],
            ids=[1001],
            start_id=0,
        )


def test_handler_upsert_is_deprecated_and_points_at_its_own_caller():
    """바로가기로 불러도 경고가 SDK 내부가 아니라 호출자 코드를 가리켜야 한다.

    `collection.upsert` 의 stacklevel=2 는 위임 프레임을 짚으므로, handler 쪽에서
    자기 경고를 내고 경고 없는 구현(`_upsert_legacy`)으로 바로 간다.
    """
    import warnings

    from vdb_manager import VDBManager

    handler = VDBManager()
    captured = {}

    class FakeCollectionWithLegacy:
        def _upsert_legacy(self, collection_name, **kwargs):
            captured.update({"collection_name": collection_name, **kwargs})
            return {"status": "upserted"}

    handler.collection = FakeCollectionWithLegacy()  # type: ignore[assignment]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        handler.upsert(
            COLLECTION, texts=["a"], dense_vectors=[[0.1]], metadatas=[{}], start_id=7
        )

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1, "경고는 한 번만 — 두 번이면 하나가 SDK 내부를 가리킨다"
    assert "upsert_points" in str(deprecations[0].message)
    assert captured["start_id"] == 7


# ------------------------------------------------------------------
# payload 수정과 index (KiwoomAM/Kiwoom-Manager#109, #110)
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("set_payload", {"ids": [1], "payload": {"file_path": "x"}}),
        ("overwrite_payload", {"ids": [1], "payload": {"file_path": "x"}}),
        ("delete_payload_keys", {"ids": [1], "keys": ["위치"]}),
        ("clear_payload", {"ids": [1]}),
        ("create_payload_index", {"field": "source_pdf", "schema": "keyword"}),
        ("delete_payload_index", {"field": "source_pdf"}),
    ],
)
def test_handler_forwards_payload_and_index_calls(method, kwargs):
    """이름과 인자가 그대로 collection 에 도착하는지 본다.

    바로가기는 시그니처를 한 벌 더 적는 일이라 두 곳이 어긋날 수 있다. 여기서
    붙잡지 않으면 인자 하나가 조용히 기본값으로 떨어진 채 서버로 간다.
    """
    handler, fake = _handler({"status": "ok"})

    getattr(handler, method)(COLLECTION, **kwargs)

    assert fake.captured == {
        "method": method,
        "collection_name": COLLECTION,
        **kwargs,
    }


def test_handler_list_payload_indexes_forwards():
    handler, fake = _handler({"source_pdf": "keyword"})

    assert handler.list_payload_indexes(COLLECTION) == {"source_pdf": "keyword"}
    assert fake.captured == {
        "method": "list_payload_indexes",
        "collection_name": COLLECTION,
    }


class _NullClient:
    """경고만 보려는 테스트를 위해 요청을 삼키는 더블."""

    def post(self, path, **kwargs):
        return {"status": "created"}


def test_slash_warning_survives_the_shortcut():
    """바로가기로 불러도 경고가 사라지지 않아야 한다.

    `stacklevel` 은 위임 프레임 때문에 호출자 줄을 정확히 짚지 못한다. 그것은
    KiwoomAM/Kiwoom-Manager#114 가 다룰 구조 문제이고, 여기서 지키는 것은 더 앞의
    계약이다 — 경고 자체가 나기는 하는가. 나지 않으면 소비자는 지울 수 없는 index 를
    만들고도 아무 말도 듣지 못한다.
    """
    import warnings

    from vdb_manager import VDBManager
    from vdb_manager.collection import CollectionClient

    handler = VDBManager()
    handler.collection = CollectionClient(_NullClient())  # type: ignore[arg-type]

    with pytest.warns(UserWarning, match="종목/분류명"):
        handler.create_payload_index(COLLECTION, field="종목/분류명", schema="keyword")


@pytest.mark.parametrize(
    "method,kwargs,http_method,path_suffix",
    [
        ("set_payload", {"ids": [1], "payload": {"a": 1}}, "PATCH", "/points/payload"),
        ("overwrite_payload", {"ids": [1], "payload": {"a": 1}}, "PUT", "/points/payload"),
        ("delete_payload_keys", {"ids": [1], "keys": ["a"]}, "DELETE", "/points/payload"),
        ("clear_payload", {"ids": [1]}, "DELETE", "/points/payload/all"),
        ("create_payload_index", {"field": "source_pdf", "schema": "keyword"}, "POST", "/indexes"),
        ("list_payload_indexes", {}, "GET", "/indexes"),
        ("delete_payload_index", {"field": "source_pdf"}, "DELETE", "/indexes"),
    ],
)
def test_shortcut_signatures_match_the_real_client(
    make_collection, method, kwargs, http_method, path_suffix
):
    """바로가기가 진짜 CollectionClient 와 맞물리는지 본다.

    위의 위임 테스트는 FakeCollection 을 주입하므로 "인자가 그대로 넘어갔는가" 까지만
    본다. 받는 쪽이 그 인자를 실제로 받을 수 있는지는 증명하지 않는다. 변이로
    확인했다 — `CollectionClient.set_payload` 의 첫 인자 이름만 `collection_name` 에서
    `name` 으로 바꾸면(같은 파일의 `create`·`info`·`delete` 가 이미 쓰는 이름이라
    정리하다 나올 수 있다) 84건이 전부 초록인 채 `handler.set_payload` 호출자가 전부
    런타임에 죽는다.

    바로가기 층을 손으로 두 벌 관리하는 한 이 구멍은 계속 생긴다. 그것을 없애는 것이
    KiwoomAM/Kiwoom-Manager#114 이고, 그때 이 테스트도 함께 사라질 것이다.
    """
    from vdb_manager import VDBManager

    collection, client = make_collection({"status": "ok", "indexes": {}})
    handler = VDBManager()
    handler.collection = collection  # type: ignore[assignment]

    getattr(handler, method)(COLLECTION, **kwargs)

    call = client.calls[0]
    assert call["method"] == http_method
    assert call["path"].endswith(path_suffix)
    if kwargs:
        assert call["json"] == kwargs
