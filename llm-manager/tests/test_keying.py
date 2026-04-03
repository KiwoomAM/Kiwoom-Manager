"""keying.py 테스트 — subset 추출, 정규화, 캐시 키 생성."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from llm_handler.models import FileRef, Part
from llm_request.keying import (
    build_llm_input_subset,
    canonicalize_input,
    canonicalize_llm_input,
    make_cache_key,
)


# ── build_llm_input_subset ────────────────────────────────────


class TestBuildLlmInputSubset:

    def test_extracts_known_fields_only(self):
        llm_input = {
            "contents": "hello",
            "model": "flash",
            "temperature": 0.5,
            "unknown_field": "should be ignored",
        }
        subset = build_llm_input_subset(llm_input)
        assert "contents" in subset
        assert "model" in subset
        assert "temperature" in subset
        assert "unknown_field" not in subset

    def test_none_values_replaced_with_empty_string(self):
        llm_input = {"contents": "hello", "system": None}
        subset = build_llm_input_subset(llm_input)
        assert subset["system"] == ""

    def test_missing_fields_included_as_empty_string(self):
        llm_input = {"contents": "hello"}
        subset = build_llm_input_subset(llm_input)
        assert subset["model"] == ""

    def test_custom_fields(self):
        llm_input = {"a": 1, "b": 2, "c": 3}
        subset = build_llm_input_subset(llm_input, fields=("a", "c"))
        assert subset == {"a": 1, "c": 3}


# ── canonicalize_llm_input ────────────────────────────────────


class TestCanonicalizeLlmInput:

    def test_deterministic_output(self):
        """같은 입력 → 항상 같은 JSON."""
        inp = {"contents": "hello", "model": "flash"}
        assert canonicalize_llm_input(inp) == canonicalize_llm_input(inp)

    def test_key_order_independent(self):
        """dict 키 순서 바뀌어도 결과 동일."""
        a = canonicalize_llm_input({"model": "flash", "contents": "hello"})
        b = canonicalize_llm_input({"contents": "hello", "model": "flash"})
        assert a == b

    def test_volatile_keys_removed(self):
        inp = {"contents": "hello", "timestamp": "2026-01-01", "request_id": "abc"}
        result = canonicalize_llm_input(inp)
        assert "timestamp" not in result
        assert "request_id" not in result
        assert "hello" in result

    def test_float_rounding(self):
        inp = {"temperature": 0.123456789}
        result = canonicalize_llm_input(inp)
        assert "0.1235" in result
        assert "0.123456789" not in result

    def test_decimal_normalization(self):
        inp = {"value": Decimal("3.14000")}
        result = canonicalize_llm_input(inp)
        assert "3.14" in result

    def test_datetime_iso_format(self):
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        inp = {"time": dt}
        result = canonicalize_llm_input(inp)
        assert "2026-01-01T12:00:00" in result

    def test_date_iso_format(self):
        d = date(2026, 1, 1)
        inp = {"date": d}
        result = canonicalize_llm_input(inp)
        assert "2026-01-01" in result

    def test_bytes_hex(self):
        inp = {"data": b"\xde\xad\xbe\xef"}
        result = canonicalize_llm_input(inp)
        assert "deadbeef" in result

    def test_none_to_empty_string(self):
        inp = {"contents": None}
        result = canonicalize_llm_input(inp)
        assert '""' in result

    def test_nested_volatile_keys(self):
        inp = {"outer": {"created_at": "123", "value": "keep"}}
        result = canonicalize_llm_input(inp)
        assert "created_at" not in result
        assert "keep" in result

    def test_list_order_preserved_by_default(self):
        inp = {"items": ["b", "a", "c"]}
        result = canonicalize_llm_input(inp)
        assert '["b","a","c"]' in result

    def test_list_order_sorted_when_disabled(self):
        result = canonicalize_llm_input(
            {"items": ["b", "a", "c"]},
            preserve_list_order=False,
        )
        assert '["a","b","c"]' in result


# ── canonicalize_input (wrapper) ──────────────────────────────


class TestCanonicalizeInput:

    def test_subset_then_canonicalize(self):
        llm_input = {
            "contents": "hello",
            "model": "flash",
            "extra_field": "ignored",
        }
        result = canonicalize_input(llm_input)
        assert "hello" in result
        assert "flash" in result
        assert "extra_field" not in result


# ── make_cache_key ────────────────────────────────────────────


class TestMakeCacheKey:

    def test_returns_64_char_hex(self):
        key = make_cache_key('{"contents":"hello"}')
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_same_input_same_key(self):
        a = make_cache_key('{"contents":"hello"}')
        b = make_cache_key('{"contents":"hello"}')
        assert a == b

    def test_different_input_different_key(self):
        a = make_cache_key('{"contents":"hello"}')
        b = make_cache_key('{"contents":"world"}')
        assert a != b

    def test_non_string_input_converted(self):
        key = make_cache_key(12345)
        assert len(key) == 64


# ── file_checksums 관련 ──────────────────────────────────────


class TestFileChecksumsInSubset:

    def test_file_checksums_included_in_subset(self):
        llm_input = {"contents": "hello", "file_checksums": ["sha256:abc"]}
        subset = build_llm_input_subset(llm_input)
        assert subset["file_checksums"] == ["sha256:abc"]

    def test_file_checksums_absent_becomes_empty(self):
        llm_input = {"contents": "hello"}
        subset = build_llm_input_subset(llm_input)
        assert subset["file_checksums"] == ""

    def test_cached_content_removed_when_file_checksums_present(self):
        llm_input = {
            "contents": "hello",
            "cached_content": "cachedContents/xyz",
            "file_checksums": ["sha256:abc"],
        }
        subset = build_llm_input_subset(llm_input)
        assert subset["cached_content"] == ""

    def test_cached_content_kept_when_no_file_checksums(self):
        llm_input = {
            "contents": "hello",
            "cached_content": "cachedContents/xyz",
        }
        subset = build_llm_input_subset(llm_input)
        assert subset["cached_content"] == "cachedContents/xyz"


class TestFileRefPartNormalization:

    def _make_file_ref(self, uri="files/abc"):
        return FileRef(name=uri, uri=f"gs://{uri}", mime_type="application/pdf", state="ACTIVE")

    def _make_inline_part(self):
        return Part(type="inline", data="base64data", mime_type="application/pdf")

    def _make_file_part(self):
        return Part(type="file", file_uri="files/abc", mime_type="image/png")

    def test_file_ref_sanitized_with_checksums(self):
        """file_checksums 있으면 FileRef의 URI가 캐시 키에서 제거됨."""
        inp = {
            "contents": ["hello", self._make_file_ref("files/abc")],
            "file_checksums": ["sha256:abc"],
        }
        result = canonicalize_llm_input(inp)
        assert "files/abc" not in result
        assert "file_ref" in result
        assert "application/pdf" in result

    def test_inline_part_sanitized_with_checksums(self):
        """file_checksums 있으면 Part inline의 base64가 캐시 키에서 제거됨."""
        inp = {
            "contents": ["hello", self._make_inline_part()],
            "file_checksums": ["sha256:abc"],
        }
        result = canonicalize_llm_input(inp)
        assert "base64data" not in result
        assert "inline_part" in result

    def test_file_part_sanitized_with_checksums(self):
        """file_checksums 있으면 Part file의 file_uri가 캐시 키에서 제거됨."""
        inp = {
            "contents": ["hello", self._make_file_part()],
            "file_checksums": ["sha256:abc"],
        }
        result = canonicalize_llm_input(inp)
        assert "file_part" in result
        assert "image/png" in result

    def test_same_checksum_different_uri_same_key(self):
        """같은 checksum + 다른 URI → 동일 캐시 키."""
        inp_a = {
            "contents": ["hello", self._make_file_ref("files/aaa")],
            "file_checksums": ["sha256:same"],
        }
        inp_b = {
            "contents": ["hello", self._make_file_ref("files/bbb")],
            "file_checksums": ["sha256:same"],
        }
        key_a = make_cache_key(canonicalize_llm_input(inp_a))
        key_b = make_cache_key(canonicalize_llm_input(inp_b))
        assert key_a == key_b

    def test_different_checksum_different_key(self):
        """다른 checksum → 다른 캐시 키."""
        inp_a = {
            "contents": ["hello", self._make_file_ref()],
            "file_checksums": ["sha256:aaa"],
        }
        inp_b = {
            "contents": ["hello", self._make_file_ref()],
            "file_checksums": ["sha256:bbb"],
        }
        key_a = make_cache_key(canonicalize_llm_input(inp_a))
        key_b = make_cache_key(canonicalize_llm_input(inp_b))
        assert key_a != key_b

    def test_same_checksum_different_cached_content_same_key(self):
        """같은 checksum + 다른 cached_content → 동일 캐시 키 (B1 패턴)."""
        inp_a = {
            "contents": "hello",
            "cached_content": "cachedContents/aaa",
            "file_checksums": ["sha256:same"],
        }
        inp_b = {
            "contents": "hello",
            "cached_content": "cachedContents/bbb",
            "file_checksums": ["sha256:same"],
        }
        key_a = make_cache_key(canonicalize_llm_input(build_llm_input_subset(inp_a)))
        key_b = make_cache_key(canonicalize_llm_input(build_llm_input_subset(inp_b)))
        assert key_a == key_b

    def test_no_file_checksums_uses_str_fallback(self):
        """file_checksums 없으면 기존 동작 유지 (str fallback)."""
        inp = {"contents": ["hello", self._make_file_ref("files/abc")]}
        result = canonicalize_llm_input(inp)
        # str(FileRef) 폴백이므로 URI가 포함됨
        assert "files/abc" in result
