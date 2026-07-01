# -*- coding: utf-8 -*-
"""_dedup standby 헬퍼 테스트 (Redis 없이 RedisManager 모킹)."""

import pytest

import file_manager._dedup as dedup


def test_standby_key_stable_and_distinct():
    m1 = {"file_name": "규약.pdf", "file_mtime": 1, "file_size": 2}
    m2 = {"file_name": "규약.pdf", "file_mtime": 1, "file_size": 2}
    m3 = {"file_name": "규약.pdf", "file_mtime": 1, "file_size": 3}  # size만 다름
    assert dedup.standby_key(m1) == dedup.standby_key(m2)   # 같은 dedup키 → 같은 standby키
    assert dedup.standby_key(m1) != dedup.standby_key(m3)   # 다른 dedup키 → 다른 standby키
    assert dedup.standby_key(m1).startswith("file_cache:register_standby:")


async def test_try_acquire_returns_setnx_result(monkeypatch):
    captured = {}

    class FakeRM:
        def setnx(self, key, value, ttl=None):
            captured["key"] = key
            captured["ttl"] = ttl
            return True

    monkeypatch.setattr(dedup, "RedisManager", lambda: FakeRM())
    ok = await dedup.try_acquire_standby("file_cache:register_standby:abc", ttl_seconds=30)
    assert ok is True
    assert captured == {"key": "file_cache:register_standby:abc", "ttl": 30}


async def test_try_acquire_maps_redis_error_to_runtimeerror(monkeypatch):
    from redis_manager import RedisUnavailableError

    class FakeRM:
        def setnx(self, key, value, ttl=None):
            raise RedisUnavailableError("redis down")

    monkeypatch.setattr(dedup, "RedisManager", lambda: FakeRM())
    with pytest.raises(RuntimeError):
        await dedup.try_acquire_standby("k")


async def test_wait_returns_cache_result_on_hit(monkeypatch):
    """성공 신호는 오직 캐시 HIT — find_cache_fn이 결과를 주면 그걸 반환."""
    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(dedup.asyncio, "sleep", _noop_sleep)

    def find_cache_fn():
        return {"hit": True, "file_path": "won.pdf"}

    out = await dedup.wait_for_standby_release("k", timeout=10, find_cache_fn=find_cache_fn)
    assert out == {"hit": True, "file_path": "won.pdf"}


async def test_wait_returns_none_on_timeout_not_on_get(monkeypatch):
    """종료 신호는 timeout뿐 — 캐시가 계속 miss여도 get 판정 없이 timeout까지 대기 후 None.

    RedisManager를 폭발시켜, 만약 get() 조기 종료가 남아있다면 예외로 드러나게 한다.
    """
    async def _noop_sleep(_):
        return None

    monkeypatch.setattr(dedup.asyncio, "sleep", _noop_sleep)

    def boom():  # get() 경로가 살아있으면 호출돼 터짐
        raise AssertionError("wait가 get()으로 조기 종료를 시도했다")

    monkeypatch.setattr(dedup, "RedisManager", boom)

    def find_cache_fn():
        return None  # 계속 miss

    out = await dedup.wait_for_standby_release("k", timeout=1, find_cache_fn=find_cache_fn)
    assert out is None
