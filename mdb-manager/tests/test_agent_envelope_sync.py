# -*- coding: utf-8 -*-
"""AgentEnvelopeCollection (sync) 단위 테스트.

mock SyncTransport 주입으로 path/body 빌더가 올바른 URL과 페이로드를 만드는지 검증.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mdb_handler.collections.agent_envelope import AgentEnvelopeCollection


def _make_col(
    collection_name: str = "agent_data", database: str = "kw_ai"
) -> tuple[AgentEnvelopeCollection, MagicMock]:
    transport = MagicMock()
    col = AgentEnvelopeCollection(
        client=transport, collection_name=collection_name, database=database
    )
    return col, transport


def test_rejects_non_envelope_collection_name():
    transport = MagicMock()
    with pytest.raises(ValueError):
        AgentEnvelopeCollection(
            client=transport, collection_name="agent_llm", database="kw_ai"
        )


def test_insert_posts_to_envelope_endpoint():
    col, t = _make_col()
    t.post.return_value = {"inserted_id": "abc"}
    result = col.insert(type="report", data={"x": 1}, ttl=3600)
    assert result == "abc"
    path = t.post.call_args[0][0]
    assert path == "/v1/kw_ai/agent_data/insert"
    body = t.post.call_args.kwargs["json"]
    assert body["type"] == "report"
    assert body["ttl"] == 3600


def test_find_posts_to_find_endpoint():
    col, t = _make_col()
    t.post.return_value = {"data": [{"_id": "x"}]}
    result = col.find({"data.k": "v"}, limit=10)
    assert result == [{"_id": "x"}]
    args = t.post.call_args
    assert args[0][0] == "/v1/kw_ai/agent_data/find"
    body = args.kwargs["json"]
    assert body["filter"] == {"data.k": "v"}
    assert body["limit"] == 10


def test_find_one_posts_to_find_one_endpoint():
    col, t = _make_col()
    t.post.return_value = {"data": {"_id": "x"}}
    col.find_one({"k": 1})
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/find_one"


def test_update_posts_to_update_endpoint():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 1}
    result = col.update({"k": 1}, {"$set": {"x": 1}}, ttl=3600)
    assert result == 1
    args = t.post.call_args
    assert args[0][0] == "/v1/kw_ai/agent_data/update"
    body = args.kwargs["json"]
    assert body["filter"] == {"k": 1}
    assert body["update"] == {"$set": {"x": 1}}
    assert body["ttl"] == 3600


def test_update_many_posts_to_update_many_endpoint():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 3}
    col.update_many({"k": 1}, {"$set": {"x": 1}})
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/update_many"


def test_delete_posts_to_delete_endpoint():
    col, t = _make_col()
    t.post.return_value = {"deleted_count": 1}
    col.delete({"k": 1})
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/delete"


def test_delete_many_posts_to_delete_many_endpoint():
    col, t = _make_col()
    t.post.return_value = {"deleted_count": 5}
    col.delete_many({"k": 1})
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/delete_many"


def test_agent_output_uses_correct_collection_in_path():
    col, t = _make_col(collection_name="agent_output")
    t.post.return_value = {"data": []}
    col.find()
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_output/find"


def test_other_database_in_path():
    col, t = _make_col(database="kw_rag")
    t.post.return_value = {"data": []}
    col.find()
    assert t.post.call_args[0][0] == "/v1/kw_rag/agent_data/find"


def test_update_passes_upsert_and_type_in_body():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 0, "upserted_id": "oid"}
    result = col.update(
        {"k": 1},
        {"$set": {"x": 1}, "$setOnInsert": {"data.k": "v"}},
        upsert=True,
        type="report",
    )
    # SDK return type stays int(modified_count) — upserted_id intentionally dropped
    assert result == 0
    body = t.post.call_args.kwargs["json"]
    assert body["upsert"] is True
    assert body["type"] == "report"
    assert body["update"]["$setOnInsert"] == {"data.k": "v"}


def test_update_default_omits_upsert_and_type():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 1, "upserted_id": None}
    col.update({"k": 1}, {"$set": {"x": 1}})
    body = t.post.call_args.kwargs["json"]
    assert "upsert" not in body
    assert "type" not in body


def test_update_upsert_with_ttl_sends_all_params():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 0, "upserted_id": "oid"}
    col.update(
        {"k": 1},
        {"$set": {"x": 1}},
        upsert=True,
        type="report",
        ttl=3600,
    )
    body = t.post.call_args.kwargs["json"]
    assert body == {
        "filter": {"k": 1},
        "update": {"$set": {"x": 1}},
        "upsert": True,
        "type": "report",
        "ttl": 3600,
    }
