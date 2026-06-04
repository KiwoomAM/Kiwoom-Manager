"""done_check.py 테스트 — 캐시 키 생성 + 조회."""

from __future__ import annotations

from unittest.mock import patch

from llm_request._internal.done_check import DoneCheckResult, done_check

SAMPLE_INPUT = {"contents": "hello", "model": "flash"}


class TestDoneCheck:

    @patch("llm_request._internal.done_check.cache_get", return_value=None)
    def test_cache_miss(self, mock_get):
        result = done_check(SAMPLE_INPUT, lookup_cache=True)

        assert isinstance(result, DoneCheckResult)
        assert result.cached is None
        assert result.cache_error is False
        assert len(result.cache_key) == 64
        assert result.collection == "agent_llm"
        mock_get.assert_called_once()

    @patch("llm_request._internal.done_check.cache_get")
    def test_cache_hit(self, mock_get):
        cached_doc = {"_id": "abc", "result": {"text": "cached"}, "status": "success"}
        mock_get.return_value = cached_doc

        result = done_check(SAMPLE_INPUT, lookup_cache=True)

        assert result.cached is not None
        assert result.cached["result"]["text"] == "cached"
        assert result.cache_error is False

    def test_no_lookup(self):
        """lookup_cache=False이면 DB 조회 없이 키만 생성."""
        result = done_check(SAMPLE_INPUT, lookup_cache=False)

        assert result.cached is None
        assert result.cache_error is False
        assert len(result.cache_key) == 64

    @patch("llm_request._internal.done_check.cache_get", side_effect=ConnectionError("db down"))
    def test_cache_error_fallback(self, mock_get):
        result = done_check(SAMPLE_INPUT, lookup_cache=True)

        assert result.cached is None
        assert result.cache_error is True
        assert len(result.cache_key) == 64

    def test_deterministic_key(self):
        """같은 입력 → 같은 키."""
        r1 = done_check(SAMPLE_INPUT, lookup_cache=False)
        r2 = done_check(SAMPLE_INPUT, lookup_cache=False)
        assert r1.cache_key == r2.cache_key

    def test_different_input_different_key(self):
        r1 = done_check({"contents": "hello"}, lookup_cache=False)
        r2 = done_check({"contents": "world"}, lookup_cache=False)
        assert r1.cache_key != r2.cache_key
