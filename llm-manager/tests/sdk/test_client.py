"""SDK client 테스트 (HTTP 호출 mocking)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_request import configure, done_check, fetch_by_cache_key, llm_request_async
from llm_request._client import AsyncClient
from llm_request.checksum import file_checksum


def test_file_checksum(tmp_path):
    """file_checksum: 파일 체크섬 계산."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = file_checksum(str(test_file))
    assert result["checksum"].startswith("sha256:")
    assert result["filename"] == "test.txt"
    assert result["size"] == 11
    assert result["mime_type"] == "text/plain"


def test_file_checksum_same_content(tmp_path):
    """같은 내용 → 같은 checksum."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("same content")
    f2.write_text("same content")

    assert file_checksum(str(f1))["checksum"] == file_checksum(str(f2))["checksum"]


def test_file_checksum_different_content(tmp_path):
    """다른 내용 → 다른 checksum."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content A")
    f2.write_text("content B")

    assert file_checksum(str(f1))["checksum"] != file_checksum(str(f2))["checksum"]


@pytest.mark.asyncio
async def test_llm_request_async_no_files():
    """파일 없이 LLM 요청."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {"text": "응답", "model": "gemini-2.5-flash"},
        "meta": {"cache_hit": False, "cache_key": "abc123"},
    }

    configure("http://test:8101")
    with patch.object(AsyncClient, "_request", new_callable=AsyncMock, return_value=mock_response):
        result, meta = await llm_request_async(
            {"contents": "테스트", "model": "gemini-2.5-flash"},
        )
        assert result["text"] == "응답"
        assert meta["cache_hit"] is False


@pytest.mark.asyncio
async def test_llm_request_async_cache_hit():
    """MDB 캐시 히트."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {"text": "캐시된 응답"},
        "meta": {"cache_hit": True, "cache_key": "abc123", "source": "cache"},
    }

    configure("http://test:8101")
    with patch.object(AsyncClient, "_request", new_callable=AsyncMock, return_value=mock_response):
        result, meta = await llm_request_async(
            {"contents": "테스트", "model": "gemini-2.5-flash"},
        )
        assert result["text"] == "캐시된 응답"
        assert meta["cache_hit"] is True
        assert meta["source"] == "cache"


@pytest.mark.asyncio
async def test_done_check():
    """done_check: MDB 캐시 확인."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "cache_key": "abc123",
        "canonical_json": "{}",
        "cached": None,
        "cache_hit": False,
        "collection": "agent_llm",
    }

    configure("http://test:8101")
    with patch.object(AsyncClient, "_request", new_callable=AsyncMock, return_value=mock_response):
        result = await done_check({"contents": "테스트", "model": "gemini-2.5-flash"})
        assert result["cache_hit"] is False
        assert result["collection"] == "agent_llm"


@pytest.mark.asyncio
async def test_fetch_by_cache_key():
    """fetch_by_cache_key: 캐시 키 조회."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {"text": "캐시된 응답"},
        "meta": {"cache_hit": True, "cache_key": "abc123"},
    }

    configure("http://test:8101")
    with patch.object(AsyncClient, "_request", new_callable=AsyncMock, return_value=mock_response):
        result, meta = await fetch_by_cache_key("abc123")
        assert result["text"] == "캐시된 응답"
        assert meta["cache_hit"] is True


def test_configure():
    """configure: 클라이언트 초기화."""
    import llm_request

    assert llm_request._instance is None
    configure("http://custom:9999", timeout=30.0)
    assert llm_request._instance is not None
