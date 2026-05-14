# -*- coding: utf-8 -*-
"""sync BlocksCollection TTL 인자 전파 검증."""

from unittest.mock import MagicMock

from mdb_handler.collections.blocks import BlocksCollection


def _make_collection() -> tuple[BlocksCollection, MagicMock]:
    client = MagicMock()
    col = BlocksCollection(client=client, collection_name="c", database="d")
    return col, client


def test_insert_one_without_ttl_omits_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-1"}
    inserted = col.insert_one({"a": 1})
    assert inserted == "id-1"
    args, kwargs = client.post.call_args
    assert args[0] == "/v1/d/c/insert_one"
    assert kwargs["json"] == {"document": {"a": 1}}


def test_insert_one_with_ttl_includes_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-1"}
    col.insert_one({"a": 1}, ttl=60)
    _, kwargs = client.post.call_args
    assert kwargs["json"] == {"document": {"a": 1}, "ttl": 60}


def test_insert_many_with_ttl_includes_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_ids": ["x", "y"]}
    col.insert_many([{"a": 1}, {"b": 2}], ttl=120)
    _, kwargs = client.post.call_args
    assert kwargs["json"] == {
        "documents": [{"a": 1}, {"b": 2}],
        "ordered": True,
        "ttl": 120,
    }


def test_insert_many_empty_skips_request():
    col, client = _make_collection()
    result = col.insert_many([], ttl=60)
    assert result == []
    client.post.assert_not_called()


def test_insert_agent_data_posts_to_envelope_endpoint():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-x"}

    result = col.insert_agent_data(
        type="report",
        metadata={"source": "u1"},
        data={"summary": "x"},
    )
    assert result == "id-x"
    args, kwargs = client.post.call_args
    assert args[0] == "/v1/d/agent_data/insert"
    assert kwargs["json"] == {
        "type": "report",
        "metadata": {"source": "u1"},
        "data": {"summary": "x"},
    }


def test_insert_agent_data_with_ttl_includes_ttl():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-x"}
    col.insert_agent_data(type="r", data={"k": "v"}, ttl=60)
    _, kwargs = client.post.call_args
    assert kwargs["json"]["ttl"] == 60


def test_insert_agent_output_posts_to_envelope_endpoint():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-y"}

    result = col.insert_agent_output(
        type="result", metadata={"task": "t1"}, data={"score": 0.9}
    )
    assert result == "id-y"
    args, kwargs = client.post.call_args
    assert args[0] == "/v1/d/agent_output/insert"
    assert kwargs["json"]["type"] == "result"
