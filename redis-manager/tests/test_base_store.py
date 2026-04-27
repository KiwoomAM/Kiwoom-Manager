# -*- coding: utf-8 -*-
"""BaseJsonStore 확장 테스트."""

import pytest
from redis_handler import BaseJsonStore, RedisHandler
from redis_handler.exceptions import RedisUnavailableError


class _TestStore(BaseJsonStore):
    def __init__(self, handler=None):
        super().__init__(prefix="test", default_ttl=60, handler=handler)


@pytest.fixture
def store():
    h = RedisHandler()
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
