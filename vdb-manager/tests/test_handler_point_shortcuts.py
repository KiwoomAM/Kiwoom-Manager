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
