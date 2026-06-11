# -*- coding: utf-8 -*-
"""v3 config 정리 핀 — Redis 직접 접속정보는 manager에 존재하지 않는다.

Redis 자격증명(비밀번호 포함)은 handler 서버만 아는 비밀이다.
get_summary()가 health_check()["config"]로 노출되므로, url 키 부재는
비밀번호 평문 노출 경로가 닫혔음을 보장한다.
"""

import pytest

from redis_manager.config import RedisConfig


def test_redis_direct_connection_attrs_removed():
    assert not hasattr(RedisConfig, "REDIS_URL")
    assert not hasattr(RedisConfig, "SOCKET_TIMEOUT")
    assert not hasattr(RedisConfig, "SOCKET_CONNECT_TIMEOUT")


def test_get_summary_has_no_url_and_keeps_handler_fields():
    summary = RedisConfig.get_summary()
    assert "url" not in summary                    # 비밀번호 노출 경로 차단
    assert "socket_timeout" not in summary
    assert "socket_connect_timeout" not in summary
    assert "handler_url" in summary
    assert "handler_timeout" in summary


def test_is_configured_based_on_handler_url():
    saved = RedisConfig.HANDLER_URL
    try:
        assert RedisConfig.is_configured() is True   # 기본값 존재
        RedisConfig.HANDLER_URL = ""
        assert RedisConfig.is_configured() is False
    finally:
        RedisConfig.HANDLER_URL = saved


def test_configure_rejects_removed_params():
    with pytest.raises(TypeError):
        RedisConfig.configure(url="redis://other:6379/0")
    with pytest.raises(TypeError):
        RedisConfig.configure(socket_timeout=10)


def test_configure_handler_url_still_works():
    saved = RedisConfig.HANDLER_URL
    try:
        RedisConfig.configure(handler_url="http://test:1234")
        assert RedisConfig.HANDLER_URL == "http://test:1234"
    finally:
        RedisConfig.configure(handler_url=saved)
