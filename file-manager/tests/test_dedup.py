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
