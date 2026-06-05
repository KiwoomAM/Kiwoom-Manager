# -*- coding: utf-8 -*-
"""BaseJsonStore 확장 테스트."""

import pytest
from redis_manager import BaseJsonStore, RedisManager
from redis_manager.exceptions import RedisUnavailableError


class _TestStore(BaseJsonStore):
    def __init__(self, handler=None):
        super().__init__(prefix="test", default_ttl=60, handler=handler)


@pytest.fixture
def store():
    h = RedisManager()
    h.reset()
    return _TestStore(handler=h)


class TestSetJsonOptions:
    def test_set_json_basic(self, store):
        assert store._set_json(store._make_key("a"), {"x": 1}) is True
        assert store._get_json(store._make_key("a")) == {"x": 1}

    def test_set_json_keepttl_propagation(self, store):
        # 메모리 폴백 환경에서 keepttl simulate
        store._set_json(store._make_key("a"), {"v": 1}, ttl=60)
        original_ttl = store._ttl(store._make_key("a"))
        store._set_json(store._make_key("a"), {"v": 2}, keepttl=True)
        new_ttl = store._ttl(store._make_key("a"))
        assert abs(new_ttl - original_ttl) <= 1

    def test_setnx_json_raises_when_redis_unavailable(self, store):
        # 메모리 폴백 환경에선 NX 차단
        with pytest.raises(RedisUnavailableError):
            store._setnx_json(store._make_key("lock"), {"owner": "A"}, ttl=30)


class TestMakeKey:
    def test_make_key_joins_with_colon(self, store):
        assert store._make_key("a", "b") == "test:a:b"

    def test_make_key_single(self, store):
        assert store._make_key("a") == "test:a"


class TestScanIter:
    def test_scan_iter_matches_prefix_pattern(self, store):
        """메모리 폴백 환경에서 prefix 매칭 검증."""
        store._set_json(store._make_key("files_api", "abc"), {"size": 1})
        store._set_json(store._make_key("files_api", "def"), {"size": 2})
        store._set_json(store._make_key("context_cache", "xyz"), {"size": 3})

        pattern = store._make_key("files_api", "*")
        keys = sorted(store._scan_iter(pattern))
        assert keys == ["test:files_api:abc", "test:files_api:def"]

    def test_scan_iter_extracts_suffix_via_prefix_strip(self, store):
        """문서 예시처럼 prefix 길이만큼 잘라 suffix만 추출하는 패턴."""
        store._set_json(store._make_key("files_api", "checksum1"), {"v": 1})
        store._set_json(store._make_key("files_api", "checksum2"), {"v": 2})

        pattern = store._make_key("files_api", "*")
        prefix_len = len(store._make_key("files_api", ""))
        suffixes = sorted(key[prefix_len:] for key in store._scan_iter(pattern))
        assert suffixes == ["checksum1", "checksum2"]

    def test_scan_iter_empty_when_no_match(self, store):
        store._set_json(store._make_key("foo"), {"v": 1})
        assert list(store._scan_iter(store._make_key("bar", "*"))) == []
