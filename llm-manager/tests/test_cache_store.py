"""file_handler/cache_store.py 테스트.

Redis 미연결 시 메모리 fallback으로 동작한다.
"""

from __future__ import annotations

from llm_request.file_handler import cache_store


class TestFilesApi:
    """Files API 레코드 CRUD 테스트."""

    def test_save_and_find(self):
        doc = {
            "file_checksum": "sha256:abc123",
            "file_name": "report.pdf",
            "file_size": 30_000_000,
            "mime_type": "application/pdf",
            "uris": ["https://example.com/files/abc"],
            "split": False,
        }
        cache_store.save_files_api("sha256:abc123", doc)
        result = cache_store.find_files_api("sha256:abc123")

        assert result is not None
        assert result["uris"] == ["https://example.com/files/abc"]
        assert result["split"] is False

    def test_find_miss_returns_none(self):
        result = cache_store.find_files_api("sha256:nonexistent")
        assert result is None

    def test_delete(self):
        doc = {
            "file_checksum": "sha256:to_delete",
            "uris": ["https://example.com/files/del"],
            "split": False,
        }
        cache_store.save_files_api("sha256:to_delete", doc)
        cache_store.delete_files_api("sha256:to_delete")

        result = cache_store.find_files_api("sha256:to_delete")
        assert result is None

    def test_split_pdf_multiple_uris(self):
        doc = {
            "file_checksum": "sha256:split_pdf",
            "file_name": "large.pdf",
            "file_size": 120_000_000,
            "mime_type": "application/pdf",
            "uris": [
                "https://example.com/files/part1",
                "https://example.com/files/part2",
                "https://example.com/files/part3",
            ],
            "split": True,
        }
        cache_store.save_files_api("sha256:split_pdf", doc)
        result = cache_store.find_files_api("sha256:split_pdf")

        assert result is not None
        assert result["split"] is True
        assert len(result["uris"]) == 3


class TestContextCache:
    """Context Cache 레코드 CRUD 테스트."""

    def test_save_and_find(self):
        doc = {
            "file_checksums": ["sha256:abc123"],
            "model": "gemini-2.5-flash",
            "system_instruction": "테스트",
            "cached_content_name": "cachedContents/xyz",
            "files_api_uris": ["https://example.com/files/abc"],
        }
        cache_store.save_context_cache("hashkey123", doc)
        result = cache_store.find_context_cache("hashkey123")

        assert result is not None
        assert result["cached_content_name"] == "cachedContents/xyz"

    def test_find_miss_returns_none(self):
        result = cache_store.find_context_cache("nonexistent_hash")
        assert result is None

    def test_delete(self):
        doc = {"cached_content_name": "cachedContents/del"}
        cache_store.save_context_cache("hashkey_del", doc)
        cache_store.delete_context_cache("hashkey_del")

        result = cache_store.find_context_cache("hashkey_del")
        assert result is None


class TestMakeContextCacheKey:
    """hash_key 생성 테스트."""

    def test_same_input_same_key(self):
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", system_instruction="test"
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", system_instruction="test"
        )
        assert key1 == key2

    def test_different_model_different_key(self):
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash"
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-pro"
        )
        assert key1 != key2

    def test_different_checksum_different_key(self):
        key1 = cache_store.make_context_cache_key(["sha256:abc"])
        key2 = cache_store.make_context_cache_key(["sha256:def"])
        assert key1 != key2

    def test_different_system_different_key(self):
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], system_instruction="A"
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], system_instruction="B"
        )
        assert key1 != key2

    def test_checksum_order_independent(self):
        """checksum 순서가 달라도 같은 키."""
        key1 = cache_store.make_context_cache_key(["sha256:a", "sha256:b"])
        key2 = cache_store.make_context_cache_key(["sha256:b", "sha256:a"])
        assert key1 == key2

    def test_returns_64_char_hex(self):
        key = cache_store.make_context_cache_key(["sha256:abc"])
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)
