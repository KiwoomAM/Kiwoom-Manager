# -*- coding: utf-8 -*-
"""import 시점 lazy 보장 회귀 테스트.

`import redis_manager`는 전역 RedisManager()를 생성하지만, HTTP 클라이언트는
첫 명령 시점까지 생성되지 않아야 한다 (v2의 "import는 연결하지 않는다" 계약의
v3 등가물). 이 보장이 깨지면 import(=pytest collection) 시점에 실네트워크로
나갈 수 있다.
"""

import importlib
import sys


def test_import_does_not_create_http_client(monkeypatch):
    """fresh import 후 전역 인스턴스의 backend는 클라이언트 미생성 상태여야 한다."""
    # 전역 싱글톤/모듈 캐시를 비우고 재import
    from redis_manager.handler import RedisManager

    saved_instance = RedisManager._instance
    RedisManager._instance = None
    saved_modules = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == "redis_manager" or name.startswith("redis_manager.")
    }
    try:
        module = importlib.import_module("redis_manager")
        backend = module.redis_manager._backend
        assert backend._http is None, "import 시점에 httpx.Client가 생성됨 — lazy 계약 위반"
    finally:
        # 재import로 생긴 싱글톤 정리 후 원래 모듈 캐시 복원
        module.redis_manager._backend.close()
        sys.modules.update(saved_modules)
        from redis_manager.handler import RedisManager as RestoredManager

        RestoredManager._instance = saved_instance


def test_configure_after_import_takes_effect(offline_manager):
    """import 후 RedisConfig.configure(handler_url=...)가 첫 사용 전이면 반영된다."""
    from redis_manager.backends.handler_backend import HandlerBackend
    from redis_manager.config import RedisConfig

    saved_url = RedisConfig.HANDLER_URL
    try:
        RedisConfig.configure(handler_url="http://configured:9999")
        backend = HandlerBackend()  # client 미주입 → lazy
        assert backend._http is None
        client = backend._ensure_client()
        assert str(client.base_url).startswith("http://configured:9999")
        backend.close()
    finally:
        RedisConfig.configure(handler_url=saved_url)
