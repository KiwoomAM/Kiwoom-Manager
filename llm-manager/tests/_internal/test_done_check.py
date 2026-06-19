"""done_check.py 테스트 — 캐시 키 생성 + 조회."""

from __future__ import annotations

from unittest.mock import patch

from llm_manager._internal.done_check import DoneCheckResult, done_check

SAMPLE_INPUT = {"contents": "hello", "model": "flash"}


class TestDoneCheck:

    @patch("llm_manager._internal.done_check.cache_get", return_value=None)
    def test_cache_miss(self, mock_get):
        result = done_check(SAMPLE_INPUT, lookup_cache=True)

        assert isinstance(result, DoneCheckResult)
        assert result.cached is None
        assert result.cache_error is False
        assert len(result.cache_key) == 64
        assert result.collection == "agent_llm"
        mock_get.assert_called_once()

    @patch("llm_manager._internal.done_check.cache_get")
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

    @patch("llm_manager._internal.done_check.cache_get", side_effect=ConnectionError("db down"))
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

    def test_dual_key_legacy_hit(self):
        """[임시 — 마이그레이션 전환기] 새 키 miss → 옛 키(service_name 포함) hit.

        라벨 제거로 새 키는 옛 엔트리를 못 맞추므로, service_name(=env)을 다시 넣은
        옛 키로 한 번 더 조회해 hit 시킨다. 반환 cache_key 는 새 키 유지.
        ★ 최종 마이그레이션 후 dual-key 로직과 함께 이 테스트도 제거.
        """
        from llm_manager._internal.keying import (
            build_llm_input_subset,
            canonicalize_input,
            canonicalize_llm_input,
            make_cache_key,
        )

        inp = {"contents": "hello", "model": "flash", "service_name": "kiwoom-rm"}
        new_key = make_cache_key(canonicalize_input(inp))
        sub = build_llm_input_subset(inp)
        sub["service_name"] = "kiwoom-rm"
        old_key = make_cache_key(canonicalize_llm_input(sub))
        assert new_key != old_key

        doc = {"_id": old_key, "result": {"text": "legacy"}}

        def fake_get(key, collection, database=None):
            return doc if key == old_key else None

        with patch("llm_manager._internal.done_check.cache_get", side_effect=fake_get):
            result = done_check(inp, lookup_cache=True)

        assert result.cached is doc          # 옛 키로 hit
        assert result.cache_key == new_key   # 반환 키는 새 키
