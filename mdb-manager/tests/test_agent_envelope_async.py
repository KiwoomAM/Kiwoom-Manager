# -*- coding: utf-8 -*-
"""AsyncAgentEnvelopeCollection 단위 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mdb_handler.aio.collections.agent_envelope import (
    AsyncAgentEnvelopeCollection,
)


def _make_col(
    collection_name: str = "agent_data", database: str = "kw_ai"
) -> tuple[AsyncAgentEnvelopeCollection, AsyncMock]:
    transport = AsyncMock()
    col = AsyncAgentEnvelopeCollection(
        client=transport, collection_name=collection_name, database=database
    )
    return col, transport


def test_async_rejects_non_envelope_collection_name():
    transport = AsyncMock()
    with pytest.raises(ValueError):
        AsyncAgentEnvelopeCollection(
            client=transport, collection_name="parsing_doc", database="kw_ai"
        )


@pytest.mark.asyncio
async def test_async_insert_posts_to_envelope_endpoint():
    col, t = _make_col()
    t.post.return_value = {"inserted_id": "abc"}
    assert await col.insert(type="r", data={}) == "abc"
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/insert"


@pytest.mark.asyncio
async def test_async_find_posts_to_find_endpoint():
    col, t = _make_col()
    t.post.return_value = {"data": []}
    assert await col.find() == []
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/find"


@pytest.mark.asyncio
async def test_async_find_one_posts_to_find_one_endpoint():
    col, t = _make_col()
    t.post.return_value = {"data": None}
    assert await col.find_one() is None
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/find_one"


@pytest.mark.asyncio
async def test_async_update_posts_to_update_endpoint():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 1}
    assert await col.update({"k": 1}, {"$set": {"x": 1}}) == 1
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/update"


@pytest.mark.asyncio
async def test_async_update_many_posts_to_update_many_endpoint():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 3}
    assert await col.update_many({"k": 1}, {"$set": {"x": 1}}) == 3
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/update_many"


@pytest.mark.asyncio
async def test_async_delete_posts_to_delete_endpoint():
    col, t = _make_col()
    t.post.return_value = {"deleted_count": 1}
    assert await col.delete({"k": 1}) == 1
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/delete"


@pytest.mark.asyncio
async def test_async_delete_many_posts_to_delete_many_endpoint():
    col, t = _make_col()
    t.post.return_value = {"deleted_count": 5}
    assert await col.delete_many({"k": 1}) == 5
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_data/delete_many"


@pytest.mark.asyncio
async def test_async_agent_output_uses_correct_collection_in_path():
    col, t = _make_col(collection_name="agent_output")
    t.post.return_value = {"data": []}
    await col.find()
    assert t.post.call_args[0][0] == "/v1/kw_ai/agent_output/find"


@pytest.mark.asyncio
async def test_async_update_passes_upsert_and_type_in_body():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 0, "upserted_id": "oid"}
    result = await col.update(
        {"k": 1},
        {"$set": {"x": 1}, "$setOnInsert": {"data.k": "v"}},
        upsert=True,
        type="report",
    )
    assert result == 0
    body = t.post.call_args.kwargs["json"]
    assert body["upsert"] is True
    assert body["type"] == "report"


@pytest.mark.asyncio
async def test_async_update_default_omits_upsert_and_type():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 1, "upserted_id": None}
    await col.update({"k": 1}, {"$set": {"x": 1}})
    body = t.post.call_args.kwargs["json"]
    assert "upsert" not in body
    assert "type" not in body


@pytest.mark.asyncio
async def test_async_update_upsert_with_ttl_sends_all_params():
    col, t = _make_col()
    t.post.return_value = {"modified_count": 0, "upserted_id": "oid"}
    await col.update(
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
