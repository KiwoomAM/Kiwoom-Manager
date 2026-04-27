# -*- coding: utf-8 -*-
"""RedisHandler.set() 입력 검증 테스트.

이 테스트는 Redis 미연결 상태에서도 ValueError가 즉시 발생해야 한다.
"""

import pytest

from redis_handler.handler import RedisHandler


@pytest.fixture
def handler():
    h = RedisHandler()
    h.reset()  # 메모리 폴백 상태로 초기화
    return h


class TestSetOptionValidation:
    def test_ttl_and_ex_raise_value_error(self, handler):
        with pytest.raises(ValueError, match="ttl과 ex"):
            handler.set("k", "v", ttl=10, ex=20)

    def test_nx_and_xx_raise_value_error(self, handler):
        with pytest.raises(ValueError, match="nx와 xx"):
            handler.set("k", "v", nx=True, xx=True)

    def test_keepttl_and_ttl_raise_value_error(self, handler):
        with pytest.raises(ValueError, match="keepttl"):
            handler.set("k", "v", ttl=60, keepttl=True)

    def test_keepttl_and_ex_raise_value_error(self, handler):
        with pytest.raises(ValueError, match="keepttl"):
            handler.set("k", "v", ex=60, keepttl=True)

    def test_legacy_ttl_signature_works(self, handler):
        """기존 set(k, v, ttl=N) 호출 패턴 호환."""
        assert handler.set("k", "v", ttl=60) is True
        assert 0 < handler.ttl("k") <= 60

    def test_ex_keyword_works(self, handler):
        assert handler.set("k", "v", ex=60) is True
        assert 0 < handler.ttl("k") <= 60

    def test_setex_wrapper(self, handler):
        assert handler.setex("k", 60, "v") is True
        assert handler.get("k") == "v"
        assert 0 < handler.ttl("k") <= 60
