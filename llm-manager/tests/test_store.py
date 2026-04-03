"""store.py 테스트 — save_result, cache_get, fetch_by_cache_key."""

from __future__ import annotations

import pytest
from llm_handler.models import LLMResponse, StreamChunk
from llm_request.store import cache_get, fetch_by_cache_key, save_result

from conftest import SAMPLE_LLM_INPUT, SAMPLE_RESPONSE


COLLECTION = "agent_llm"
CACHE_KEY = "test-key-abc123"
CANONICAL = '{"contents":"안녕하세요"}'


class TestSaveResult:
    """save_result: 문서 저장 검증."""

    def test_saved_doc_has_result(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"model": "flash"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert doc["result"]["text"] == "응답입니다"
        assert doc["canonical_json"] == CANONICAL

    def test_saved_doc_has_elapsed_ms(self, mock_collection):
        from datetime import UTC, datetime

        requested_at = datetime.now(UTC)
        save_result(
            CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {}, COLLECTION,
            requested_at=requested_at,
        )
        doc = mock_collection[CACHE_KEY]
        assert "elapsed_ms" in doc
        assert "requested_at" in doc


class TestCacheGet:
    """cache_get: 문서 조회 확인."""

    def test_returns_saved_doc(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {}, COLLECTION)
        assert cache_get(CACHE_KEY, COLLECTION) is not None

    def test_returns_none_when_missing(self, mock_collection):
        assert cache_get("nonexistent-key", COLLECTION) is None


class TestFetchByCacheKey:
    """fetch_by_cache_key: cache_key로 직접 조회 + LLMResponse/StreamChunk 변환."""

    def test_returns_llm_response(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"model": "flash"}, COLLECTION)
        response, meta = fetch_by_cache_key(CACHE_KEY)

        assert isinstance(response, LLMResponse)
        assert response.text == "응답입니다"
        assert meta["source"] == "cache"
        assert meta["cache_hit"] is True
        assert meta["cache_key"] == CACHE_KEY
        assert meta["collection"] == "agent_llm"

    def test_returns_stream_chunk(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"model": "flash"}, COLLECTION)
        stream_iter, meta = fetch_by_cache_key(CACHE_KEY, as_stream=True)

        chunks = list(stream_iter)
        assert len(chunks) == 1
        assert isinstance(chunks[0], StreamChunk)
        assert chunks[0].text == "응답입니다"
        assert meta["source"] == "cache"

    def test_raises_key_error_when_missing(self, mock_collection):
        with pytest.raises(KeyError):
            fetch_by_cache_key("nonexistent-key")

    def test_meta_contains_metadata(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"model": "flash"}, COLLECTION)
        _, meta = fetch_by_cache_key(CACHE_KEY)

        assert "metadata" in meta
        assert meta["metadata"]["model"] == "flash"
