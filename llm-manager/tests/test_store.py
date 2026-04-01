"""store.py 테스트 — save_result, save_error, cache_get."""

from __future__ import annotations

from llm_handler.exceptions import HandlerError

from llm_request.store import cache_get, save_error, save_result

from conftest import SAMPLE_LLM_INPUT, SAMPLE_RESPONSE


COLLECTION = "agent_llm"
CACHE_KEY = "test-key-abc123"
CANONICAL = '{"contents":"안녕하세요"}'


class TestSaveResult:
    """save_result: status=success 포함 여부."""

    def test_saved_doc_has_status_success(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {"model": "flash"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert doc["status"] == "success"
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


class TestSaveError:
    """save_error: status=error 및 에러 상세 저장."""

    def test_basic_exception(self, mock_collection):
        exc = RuntimeError("something broke")
        save_error(CACHE_KEY, CANONICAL, exc, {"model": "flash"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert doc["status"] == "error"
        assert doc["error"]["type"] == "RuntimeError"
        assert "something broke" in doc["error"]["message"]
        assert "status_code" not in doc["error"]

    def test_handler_error_with_attributes(self, mock_collection):
        exc = HandlerError("provider down", status_code=503, error_type="provider")
        save_error(CACHE_KEY, CANONICAL, exc, {"model": "flash"}, COLLECTION)

        doc = mock_collection[CACHE_KEY]
        assert doc["status"] == "error"
        assert doc["error"]["type"] == "HandlerError"
        assert doc["error"]["status_code"] == 503
        assert doc["error"]["error_type"] == "provider"

    def test_error_doc_has_elapsed_ms(self, mock_collection):
        from datetime import UTC, datetime

        exc = RuntimeError("fail")
        save_error(
            CACHE_KEY, CANONICAL, exc, {}, COLLECTION,
            requested_at=datetime.now(UTC),
        )
        doc = mock_collection[CACHE_KEY]
        assert "elapsed_ms" in doc


class TestCacheGet:
    """cache_get: 에러 문서 제외 확인."""

    def test_returns_success_doc(self, mock_collection):
        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {}, COLLECTION)
        assert cache_get(CACHE_KEY, COLLECTION) is not None

    def test_excludes_error_doc(self, mock_collection):
        exc = RuntimeError("fail")
        save_error(CACHE_KEY, CANONICAL, exc, {}, COLLECTION)
        assert cache_get(CACHE_KEY, COLLECTION) is None

    def test_error_overwritten_by_success(self, mock_collection):
        exc = RuntimeError("fail")
        save_error(CACHE_KEY, CANONICAL, exc, {}, COLLECTION)
        assert cache_get(CACHE_KEY, COLLECTION) is None

        save_result(CACHE_KEY, CANONICAL, SAMPLE_RESPONSE, {}, COLLECTION)
        doc = cache_get(CACHE_KEY, COLLECTION)
        assert doc is not None
        assert doc["status"] == "success"
