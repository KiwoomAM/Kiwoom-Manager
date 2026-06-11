# -*- coding: utf-8 -*-
"""aio 패키지 public export 스모크 테스트."""


def test_aio_exports_async_classes():
    from redis_manager.aio import AsyncBaseJsonStore, AsyncRedisManager

    assert AsyncRedisManager is not None
    assert AsyncBaseJsonStore is not None


def test_top_level_exposes_aio():
    import redis_manager

    assert hasattr(redis_manager, "aio")
    assert redis_manager.aio.AsyncRedisManager is not None


def test_importing_aio_does_not_create_global_instance():
    """sync처럼 import 시 전역 인스턴스를 만들지 않는다 (httpx.AsyncClient 루프 바인딩 회피)."""
    import redis_manager.aio as aio

    # 모듈 레벨 전역 manager 인스턴스가 없어야 함
    assert not any(
        type(v).__name__ == "AsyncRedisManager" for v in vars(aio).values()
    )
