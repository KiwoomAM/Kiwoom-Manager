# -*- coding: utf-8 -*-
"""async AsyncBlocksCollection TTL 인자 전파 검증."""

from unittest.mock import AsyncMock

import pytest

from mdb_handler.aio.collections.blocks import AsyncBlocksCollection


def _make_collection() -> tuple[AsyncBlocksCollection, AsyncMock]:
    client = AsyncMock()
    col = AsyncBlocksCollection(client=client, collection_name="c", database="d")
    return col, client


@pytest.mark.asyncio
async def test_insert_one_without_ttl_omits_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-1"}
    inserted = await col.insert_one({"a": 1})
    assert inserted == "id-1"
    _, kwargs = client.post.call_args
    assert kwargs["json"] == {"document": {"a": 1}}


@pytest.mark.asyncio
async def test_insert_one_with_ttl_includes_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_id": "id-1"}
    await col.insert_one({"a": 1}, ttl=60)
    _, kwargs = client.post.call_args
    assert kwargs["json"] == {"document": {"a": 1}, "ttl": 60}


@pytest.mark.asyncio
async def test_insert_many_with_ttl_includes_ttl_in_body():
    col, client = _make_collection()
    client.post.return_value = {"inserted_ids": ["x"]}
    await col.insert_many([{"a": 1}], ttl=300)
    _, kwargs = client.post.call_args
    assert kwargs["json"] == {
        "documents": [{"a": 1}],
        "ordered": True,
        "ttl": 300,
    }


@pytest.mark.asyncio
async def test_insert_many_empty_skips_request():
    col, client = _make_collection()
    result = await col.insert_many([], ttl=60)
    assert result == []
    client.post.assert_not_called()
