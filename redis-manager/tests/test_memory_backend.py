# -*- coding: utf-8 -*-
"""MemoryBackend 단위 테스트."""

import time
import pytest
from redis_handler.backends.memory_backend import MemoryBackend


class TestMemoryBackendBasic:
    def test_name_is_memory(self):
        assert MemoryBackend().name == "memory"

    def test_set_and_get(self):
        b = MemoryBackend()
        assert b.set("k", "v") is True
        assert b.get("k") == "v"

    def test_get_missing_returns_none(self):
        assert MemoryBackend().get("missing") is None

    def test_delete_returns_1_when_existed(self):
        b = MemoryBackend()
        b.set("k", "v")
        assert b.delete("k") == 1
        assert b.get("k") is None

    def test_delete_returns_0_when_missing(self):
        assert MemoryBackend().delete("missing") == 0

    def test_exists_true_for_existing(self):
        b = MemoryBackend()
        b.set("k", "v")
        assert b.exists("k") is True

    def test_exists_false_for_missing(self):
        assert MemoryBackend().exists("missing") is False


class TestMemoryBackendTTL:
    def test_set_with_ex_sets_expiry(self):
        b = MemoryBackend()
        b.set("k", "v", ex=60)
        assert 0 < b.ttl("k") <= 60

    def test_ttl_minus_1_for_no_ttl(self):
        b = MemoryBackend()
        b.set("k", "v")
        assert b.ttl("k") == -1

    def test_ttl_minus_2_for_missing(self):
        assert MemoryBackend().ttl("missing") == -2

    def test_expired_key_returns_none(self):
        b = MemoryBackend()
        b.set("k", "v", ex=1)
        time.sleep(1.1)
        assert b.get("k") is None
        assert b.ttl("k") == -2

    def test_expire_sets_ttl(self):
        b = MemoryBackend()
        b.set("k", "v")
        assert b.expire("k", 60) is True
        assert 0 < b.ttl("k") <= 60

    def test_expire_returns_false_for_missing(self):
        assert MemoryBackend().expire("missing", 60) is False


class TestMemoryBackendNX:
    def test_nx_succeeds_when_key_missing(self):
        b = MemoryBackend()
        assert b.set("k", "v1", nx=True) is True
        assert b.get("k") == "v1"

    def test_nx_fails_when_key_exists(self):
        b = MemoryBackend()
        b.set("k", "v1")
        assert b.set("k", "v2", nx=True) is False
        assert b.get("k") == "v1"  # 변경 안 됨

    def test_nx_succeeds_after_expiry(self):
        b = MemoryBackend()
        b.set("k", "v1", ex=1)
        time.sleep(1.1)
        assert b.set("k", "v2", nx=True) is True
        assert b.get("k") == "v2"


class TestMemoryBackendXX:
    def test_xx_succeeds_when_key_exists(self):
        b = MemoryBackend()
        b.set("k", "v1")
        assert b.set("k", "v2", xx=True) is True
        assert b.get("k") == "v2"

    def test_xx_fails_when_key_missing(self):
        b = MemoryBackend()
        assert b.set("k", "v", xx=True) is False
        assert b.get("k") is None


class TestMemoryBackendKeepTTL:
    def test_keepttl_preserves_existing_expiry(self):
        b = MemoryBackend()
        b.set("k", "v1", ex=60)
        original_ttl = b.ttl("k")
        b.set("k", "v2", keepttl=True)
        assert b.get("k") == "v2"
        # TTL은 유지되어야 함 (정확한 값 비교 대신 근사)
        assert abs(b.ttl("k") - original_ttl) <= 1

    def test_set_without_keepttl_clears_expiry(self):
        b = MemoryBackend()
        b.set("k", "v1", ex=60)
        b.set("k", "v2")  # keepttl 없음 → TTL 제거
        assert b.ttl("k") == -1


class TestMemoryBackendSet:
    def test_sadd_and_smembers(self):
        b = MemoryBackend()
        assert b.sadd("s", "a", "b", "c") == 3
        assert b.smembers("s") == {"a", "b", "c"}

    def test_sadd_duplicate_returns_0(self):
        b = MemoryBackend()
        b.sadd("s", "a")
        assert b.sadd("s", "a") == 0

    def test_srem_removes_members(self):
        b = MemoryBackend()
        b.sadd("s", "a", "b", "c")
        assert b.srem("s", "a", "b") == 2
        assert b.smembers("s") == {"c"}

    def test_smembers_missing_returns_empty(self):
        assert MemoryBackend().smembers("missing") == set()


class TestMemoryBackendPing:
    def test_ping_always_returns_true(self):
        # 메모리 백엔드는 항상 살아있음
        assert MemoryBackend().ping() is True


class TestMemoryBackendScanIter:
    def test_empty_backend_yields_nothing(self):
        b = MemoryBackend()
        assert list(b.scan_iter("*")) == []

    def test_matches_string_keys(self):
        b = MemoryBackend()
        b.set("file_cache:files_api:abc", "v")
        b.set("file_cache:files_api:def", "v")
        b.set("other:key", "v")
        keys = sorted(b.scan_iter("file_cache:files_api:*"))
        assert keys == ["file_cache:files_api:abc", "file_cache:files_api:def"]

    def test_matches_set_keys(self):
        """_sets에만 존재하는 키도 yield 되어야 함."""
        b = MemoryBackend()
        b.sadd("group:a", "x")
        b.sadd("group:b", "y")
        b.set("string:c", "v")
        keys = sorted(b.scan_iter("group:*"))
        assert keys == ["group:a", "group:b"]

    def test_excludes_expired_keys(self):
        b = MemoryBackend()
        b.set("alive:a", "v", ex=60)
        b.set("dying:b", "v", ex=1)
        time.sleep(1.1)
        keys = list(b.scan_iter("*:*"))
        assert "alive:a" in keys
        assert "dying:b" not in keys

    def test_no_match_yields_empty(self):
        b = MemoryBackend()
        b.set("foo", "v")
        assert list(b.scan_iter("bar:*")) == []

    def test_wildcard_position_variants(self):
        b = MemoryBackend()
        b.set("prefix:abc:suffix", "v")
        b.set("prefix:xyz:suffix", "v")
        b.set("prefix:abc:other", "v")
        assert sorted(b.scan_iter("prefix:*:suffix")) == [
            "prefix:abc:suffix",
            "prefix:xyz:suffix",
        ]
        assert sorted(b.scan_iter("*:abc:*")) == [
            "prefix:abc:other",
            "prefix:abc:suffix",
        ]

    def test_count_argument_is_ignored_but_accepted(self):
        b = MemoryBackend()
        b.set("k", "v")
        # 인터페이스 호환을 위해 count 수신만 하고 결과에 영향 없음
        assert list(b.scan_iter("*", count=1)) == ["k"]
        assert list(b.scan_iter("*", count=10000)) == ["k"]

    def test_string_and_set_keys_both_included(self):
        """동일 prefix를 가진 string/set 키가 모두 yield되는지."""
        b = MemoryBackend()
        b.set("p:string", "v")
        b.sadd("p:set", "m")
        assert sorted(b.scan_iter("p:*")) == ["p:set", "p:string"]


class TestMemoryBackendCleanup:
    def test_cleanup_expired_removes_only_expired(self):
        b = MemoryBackend()
        b.set("alive", "v", ex=60)
        b.set("dying", "v", ex=1)
        time.sleep(1.1)
        removed = b.cleanup_expired()
        assert removed == 1
        assert b.get("alive") == "v"
        assert b.get("dying") is None
