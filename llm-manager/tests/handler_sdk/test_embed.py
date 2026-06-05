"""SDK embed() 테스트 (sync + async)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import llm_request._internal.handler_client
import llm_request._internal.handler_client.aio
from llm_request._internal.handler_client.exceptions import HandlerError


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )


_EMBED_RESPONSE = {
    "embeddings": [
        {
            "values": [0.1, 0.2, 0.3],
            "statistics": {"token_count": 5, "truncated": False},
        }
    ],
    "model": "gemini-embedding-001",
    "request_id": "embed-1",
}

_BATCH_EMBED_RESPONSE = {
    "embeddings": [
        {"values": [0.1, 0.2, 0.3], "statistics": {"token_count": 3, "truncated": False}},
        {"values": [0.4, 0.5, 0.6], "statistics": {"token_count": 4, "truncated": False}},
    ],
    "model": "gemini-embedding-001",
    "request_id": "embed-2",
}


# ── Sync ──


def test_embed_single_text():
    """단일 텍스트 임베딩."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(200, _EMBED_RESPONSE)
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_request._internal.handler_client.embed("hello world")

    assert len(result.embeddings) == 1
    assert result.embeddings[0].values == [0.1, 0.2, 0.3]
    assert result.embeddings[0].statistics is not None
    assert result.embeddings[0].statistics.token_count == 5
    assert result.model == "gemini-embedding-001"

    body = instance.request.call_args.kwargs["json"]
    assert body["contents"] == "hello world"


def test_embed_batch():
    """배치 텍스트 임베딩."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(200, _BATCH_EMBED_RESPONSE)
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        result = llm_request._internal.handler_client.embed(["text1", "text2"])

    assert len(result.embeddings) == 2
    body = instance.request.call_args.kwargs["json"]
    assert body["contents"] == ["text1", "text2"]


def test_embed_with_options():
    """모든 옵션 전달."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(200, _EMBED_RESPONSE)
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        llm_request._internal.handler_client.embed(
            "query text",
            model="gemini-embedding-001",
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768,
        )

    body = instance.request.call_args.kwargs["json"]
    assert body["model"] == "gemini-embedding-001"
    assert body["task_type"] == "RETRIEVAL_QUERY"
    assert body["output_dimensionality"] == 768


def test_embed_with_title():
    """title 파라미터 전달."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(200, _EMBED_RESPONSE)
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        llm_request._internal.handler_client.embed(
            "document content",
            task_type="RETRIEVAL_DOCUMENT",
            title="My Document",
        )

    body = instance.request.call_args.kwargs["json"]
    assert body["task_type"] == "RETRIEVAL_DOCUMENT"
    assert body["title"] == "My Document"


def test_embed_none_options_excluded():
    """None인 옵션은 body에서 제외."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(200, _EMBED_RESPONSE)
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        llm_request._internal.handler_client.embed("text")

    body = instance.request.call_args.kwargs["json"]
    assert "model" not in body
    assert "task_type" not in body
    assert "title" not in body
    assert "output_dimensionality" not in body


def test_embed_handler_error():
    """Handler 에러 전파."""
    with patch("llm_request._internal.handler_client._sync_client.httpx.Client") as mock_cls:
        instance = mock_cls.return_value
        instance.request.return_value = _mock_response(429, {"error": "rate limit"})
        llm_request._internal.handler_client.configure(base_url="http://test:8100")
        with pytest.raises(HandlerError) as exc_info:
            llm_request._internal.handler_client.embed("test")
    assert exc_info.value.status_code == 429


# ── Async ──


@pytest.mark.asyncio
async def test_async_embed_single_text():
    """비동기 단일 텍스트 임베딩."""
    with patch("llm_request._internal.handler_client._client.httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _EMBED_RESPONSE))
        llm_request._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_request._internal.handler_client.aio.embed("hello world")

    assert len(result.embeddings) == 1
    assert result.embeddings[0].values == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_async_embed_batch():
    """비동기 배치 텍스트 임베딩."""
    with patch("llm_request._internal.handler_client._client.httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value
        instance.request = AsyncMock(return_value=_mock_response(200, _BATCH_EMBED_RESPONSE))
        llm_request._internal.handler_client.aio.configure(base_url="http://test:8100")
        result = await llm_request._internal.handler_client.aio.embed(["text1", "text2"])

    assert len(result.embeddings) == 2
