"""file_processing/cache_store.py 테스트.

Redis 미연결 시 메모리 fallback으로 동작한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from llm_handler.models import FileRef

from app.logic.file_processing import cache_store
from app.logic.file_processing.cache_store import (
    FILES_API_TTL,
    FILES_API_TTL_FLOOR,
    compute_files_api_ttl,
)


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

    def test_different_tools_different_key(self):
        """같은 파일·모델·system 이라도 tools 다르면 별도 cache_key.

        이전 버그: cache_key 재료에 tools 미포함 → tools=A 로 만든 cache 가
        tools=B 요청에 HIT → llm-handler 가 cache 의 tools 적용 → 잘못된 결과.
        """
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash",
            tools=[{"name": "calculator"}],
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash",
            tools=[{"name": "search"}],
        )
        assert key1 != key2

    def test_different_tool_config_different_key(self):
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", tool_config="auto",
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", tool_config="any",
        )
        assert key1 != key2

    def test_tools_none_and_empty_list_same_key(self):
        """`tools=None` 과 `tools=[]` 는 동일 키 (None → `or []` 정규화).

        caller 가 명시적으로 빈 리스트를 넘겨도 미지정과 동일 cache 를 공유 —
        의도된 동작이며 cache 효율을 위해 보장.
        """
        key1 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", tools=None,
        )
        key2 = cache_store.make_context_cache_key(
            ["sha256:abc"], model="gemini-2.5-flash", tools=[],
        )
        assert key1 == key2

    def test_returns_64_char_hex(self):
        key = cache_store.make_context_cache_key(["sha256:abc"])
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestComputeFilesApiTtl:
    """compute_files_api_ttl 동적 TTL 계산 테스트."""

    def _ref(self, expiration: datetime | None = None) -> FileRef:
        return FileRef(
            name="files/test",
            uri="https://example.com/test",
            mime_type="application/pdf",
            state="ACTIVE",
            expiration_time=expiration.isoformat() if expiration else None,
        )

    def test_all_refs_have_expiration(self):
        """정상 케이스 — 48h 미래 만료, 1h buffer → 약 47h."""
        future = datetime.now(UTC) + timedelta(hours=48)
        refs = [self._ref(future)]
        ttl = compute_files_api_ttl(refs)
        # 47h 근처, 실행 시간 오차 허용
        assert abs(ttl - FILES_API_TTL) < 10

    def test_fallback_when_missing_expiration(self):
        """expiration_time 없으면 FILES_API_TTL 폴백."""
        refs = [self._ref(None)]
        ttl = compute_files_api_ttl(refs)
        assert ttl == FILES_API_TTL

    def test_fallback_when_partial_missing(self):
        """일부라도 expiration_time 없으면 폴백."""
        future = datetime.now(UTC) + timedelta(hours=48)
        refs = [self._ref(future), self._ref(None)]
        ttl = compute_files_api_ttl(refs)
        assert ttl == FILES_API_TTL

    def test_floor_when_remaining_under_1h(self):
        """만료 30분 전이면 1h floor 적용 (30분 - 1h buffer = 음수)."""
        future = datetime.now(UTC) + timedelta(minutes=30)
        refs = [self._ref(future)]
        ttl = compute_files_api_ttl(refs)
        assert ttl == FILES_API_TTL_FLOOR

    def test_floor_when_already_expired(self):
        """이미 만료 → 1h floor."""
        past = datetime.now(UTC) - timedelta(hours=1)
        refs = [self._ref(past)]
        ttl = compute_files_api_ttl(refs)
        assert ttl == FILES_API_TTL_FLOOR

    def test_split_pdf_uses_earliest_expiration(self):
        """여러 파트 중 가장 이른 만료 채택."""
        early = datetime.now(UTC) + timedelta(hours=10)
        late = datetime.now(UTC) + timedelta(hours=48)
        refs = [self._ref(late), self._ref(early)]
        ttl = compute_files_api_ttl(refs)
        # 10h - 1h buffer = 9h
        expected = int(timedelta(hours=9).total_seconds())
        assert abs(ttl - expected) < 10

    def test_invalid_iso_string_fallback(self):
        """ISO 파싱 실패 → 폴백."""
        ref = FileRef(
            name="files/x", uri="u", mime_type="application/pdf", state="ACTIVE",
            expiration_time="not-iso",
        )
        ttl = compute_files_api_ttl([ref])
        assert ttl == FILES_API_TTL

    def test_empty_refs_fallback(self):
        """빈 리스트 → 폴백 (방어)."""
        ttl = compute_files_api_ttl([])
        assert ttl == FILES_API_TTL

    def test_custom_buffer_seconds(self):
        """buffer_seconds 커스텀 — 3h buffer."""
        future = datetime.now(UTC) + timedelta(hours=48)
        refs = [self._ref(future)]
        ttl = compute_files_api_ttl(refs, buffer_seconds=3 * 3600)
        # 48h - 3h = 45h
        expected = int(timedelta(hours=45).total_seconds())
        assert abs(ttl - expected) < 10
