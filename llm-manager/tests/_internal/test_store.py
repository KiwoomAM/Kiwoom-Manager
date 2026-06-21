"""store.py 테스트 — save_result, cache_get, fetch_by_cache_key."""

from __future__ import annotations

import pytest
from conftest import SAMPLE_RESPONSE

from llm_manager._internal.handler_client.models import LLMResponse
from llm_manager._internal.store import cache_get, fetch_by_cache_key, save_result

COLLECTION = "agent_llm"
CACHE_KEY = "test-key-abc123"
CANONICAL = '{"contents":"안녕하세요"}'


class TestSaveResult:
    """save_result: 문서 저장 검증."""

    def test_saved_doc_has_result(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service", "model": "flash"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert doc["result"]["text"] == "응답입니다"
        assert doc["canonical_json"] == CANONICAL

    def test_saved_doc_has_requested_at(self, mock_collection):
        from datetime import UTC, datetime

        requested_at = datetime.now(UTC)
        save_result(
            CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service"}, COLLECTION,
            requested_at=requested_at,
        )
        doc = mock_collection[CACHE_KEY]
        assert "requested_at" in doc
        assert "elapsed_ms" not in doc


    def test_saved_doc_with_file_meta(self, mock_collection):
        file_metas = [
            {"checksum": "sha256:abc", "filename": "report.pdf", "size": 12345, "mime_type": "application/pdf"}
        ]
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service"}, COLLECTION, file_metas=file_metas)

        doc = mock_collection[CACHE_KEY]
        # 저장 스키마 호환을 위해 doc 키는 기존 "file_meta" 유지 (store.py 주석 참조)
        assert doc["file_meta"] == file_metas

    def test_saved_doc_without_file_meta(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert "file_meta" not in doc


class TestCacheGet:
    """cache_get: 문서 조회 확인."""

    def test_returns_saved_doc(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service"}, COLLECTION)
        assert cache_get(CACHE_KEY, COLLECTION) is not None

    def test_returns_none_when_missing(self, mock_collection):
        assert cache_get("nonexistent-key", COLLECTION) is None


class TestFetchByCacheKey:
    """fetch_by_cache_key: cache_key로 직접 조회."""

    def test_returns_llm_response(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service", "model": "flash"}, COLLECTION)
        response, meta = fetch_by_cache_key(CACHE_KEY)

        assert isinstance(response, LLMResponse)
        assert response.text == "응답입니다"
        assert meta["source"] == "cache"
        assert meta["cache_hit"] is True
        assert meta["cache_key"] == CACHE_KEY
        assert meta["collection"] == "agent_llm"

    def test_raises_key_error_when_missing(self, mock_collection):
        with pytest.raises(KeyError):
            fetch_by_cache_key("nonexistent-key")

    def test_meta_contains_metadata(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service", "model": "flash"}, COLLECTION)
        _, meta = fetch_by_cache_key(CACHE_KEY)

        assert "metadata" in meta
        assert meta["metadata"]["model"] == "flash"


class TestFetchByCacheKeyFileMeta:
    """fetch_by_cache_key 가 doc 의 file_meta 를 응답 meta 에 노출하는지 (Phase 1)."""

    def test_returns_file_meta_when_doc_has_it(self, mock_collection):
        file_metas = [{
            "file_name": "x.pdf", "file_size": 1, "file_mtime": 0,
            "mime_type": "application/pdf", "extension": ".pdf",
            "file_path": "abc-uuid.pdf",
        }]
        save_result(
            CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service", "model": "flash"},
            COLLECTION, file_metas=file_metas,
        )

        _, meta = fetch_by_cache_key(CACHE_KEY)

        assert "file_meta" in meta
        assert meta["file_meta"][0]["file_path"] == "abc-uuid.pdf"

    def test_omits_file_meta_for_legacy_doc(self, mock_collection):
        """file_meta 키 없는 옛 doc → 응답에도 키 없음."""
        save_result(
            CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"service_name": "test-service", "model": "flash"},
            COLLECTION,
            # file_metas 인자 생략 → doc 에 file_meta 키 없음
        )

        _, meta = fetch_by_cache_key(CACHE_KEY)

        assert "file_meta" not in meta
