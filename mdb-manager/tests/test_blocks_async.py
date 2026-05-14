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


@pytest.mark.asyncio
async def test_async_insert_agent_data_posts_to_envelope_endpoint():
    from mdb_handler.aio.collections.blocks import AsyncBlocksCollection
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.post = AsyncMock(return_value={"inserted_id": "id-x"})
    col = AsyncBlocksCollection(client=client, collection_name="c", database="d")

    result = await col.insert_agent_data(
        type="report", metadata={"s": "u"}, data={"k": "v"}
    )
    assert result == "id-x"
    args, kwargs = client.post.call_args
    assert args[0] == "/v1/d/agent_data/insert"
    assert kwargs["json"] == {
        "type": "report",
        "metadata": {"s": "u"},
        "data": {"k": "v"},
    }


@pytest.mark.asyncio
async def test_async_insert_agent_output_posts_to_envelope_endpoint():
    from mdb_handler.aio.collections.blocks import AsyncBlocksCollection
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.post = AsyncMock(return_value={"inserted_id": "id-y"})
    col = AsyncBlocksCollection(client=client, collection_name="c", database="d")

    result = await col.insert_agent_output(type="r", data={"s": 1.0})
    assert result == "id-y"
    args, _ = client.post.call_args
    assert args[0] == "/v1/d/agent_output/insert"


@pytest.mark.asyncio
async def test_async_insert_agent_data_emits_deprecation_warning():
    import warnings
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.post = AsyncMock(return_value={"inserted_id": "x"})
    col = AsyncBlocksCollection(client=client, collection_name="c", database="d")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        await col.insert_agent_data(type="r", data={})
    deprecations = [
        x for x in w if issubclass(x.category, DeprecationWarning)
    ]
    assert deprecations, "DeprecationWarning expected"
    msg = " ".join(str(x.message) for x in deprecations)
    assert "AsyncAgentEnvelopeCollection" in msg
