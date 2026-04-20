"""file_processing/dedup.py 테스트.

get_client()를 in-memory fake Redis 클라이언트로 mock하여
실제 Redis 없이 SETNX/GET/DEL 동작을 검증한다.

공유 fixture(fake_redis/no_redis/fast_backoff/FakeRedisClient)는
tests/logic/conftest.py 에 정의되어 있다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.logic.file_processing import dedup
from app.logic.file_processing.dedup import (
    _make_standby_key,
    release_standby,
    try_acquire_standby,
    wait_for_standby_release,
)


# ── _make_standby_key ────────────────────────────────────────


class TestMakeStandbyKey:
    def test_files_api_prefix(self):
        assert _make_standby_key("files_api", "abc123") == "file_cache:files_api_standby:abc123"

    def test_context_cache_prefix(self):
        assert _make_standby_key("context_cache", "h1") == "file_cache:context_cache_standby:h1"

    def test_agent_llm_prefix(self):
        assert _make_standby_key("agent_llm", "ck1") == "agent_llm_standby:ck1"

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Unknown standby prefix"):
            _make_standby_key("unknown_prefix", "x")


# ── try_acquire_standby ──────────────────────────────────────


class TestTryAcquireStandby:
    async def test_first_acquire_returns_true(self, fake_redis):
        ok = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        assert ok is True
        # 실제 키 저장 확인
        assert fake_redis.get("file_cache:files_api_standby:checksum1") is not None

    async def test_second_acquire_returns_false(self, fake_redis):
        ok1 = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        ok2 = await try_acquire_standby("files_api", "checksum1", ttl_seconds=60)
        assert ok1 is True
        assert ok2 is False

    async def test_different_keys_both_acquire(self, fake_redis):
        ok1 = await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        ok2 = await try_acquire_standby("files_api", "k2", ttl_seconds=60)
        assert ok1 is True
        assert ok2 is True

    async def test_different_prefix_no_conflict(self, fake_redis):
        """같은 key라도 prefix 다르면 네임스페이스 분리."""
        ok1 = await try_acquire_standby("files_api", "shared", ttl_seconds=60)
        ok2 = await try_acquire_standby("context_cache", "shared", ttl_seconds=60)
        ok3 = await try_acquire_standby("agent_llm", "shared", ttl_seconds=60)
        assert (ok1, ok2, ok3) == (True, True, True)

    async def test_redis_unavailable_raises(self, no_redis):
        with pytest.raises(RuntimeError, match="Redis 미연결"):
            await try_acquire_standby("files_api", "x", ttl_seconds=60)


# ── wait_for_standby_release ──────────────────────────────────


class TestWaitForStandbyRelease:
    async def test_sync_find_cache_fn_hit_returns_immediately(self, fake_redis, fast_backoff):
        """sync find_cache_fn 이 HIT 반환 시 즉시 종료."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        cached_doc = {"uris": ["u1"]}
        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=lambda: cached_doc,
        )
        assert result == cached_doc

    async def test_async_find_cache_fn_hit_returns_immediately(self, fake_redis, fast_backoff):
        """async find_cache_fn 도 동일하게 지원."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        cached_doc = {"cached_content_name": "cc1"}

        async def _find():
            return cached_doc

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=_find,
        )
        assert result == cached_doc

    async def test_standby_released_miss_returns_none(self, fake_redis, fast_backoff):
        """standby 삭제 + 캐시 MISS → None 반환 (호출자가 작업)."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        # 다른 태스크가 standby 해제 + 캐시는 여전히 MISS (실패 시나리오)
        async def _releaser():
            await asyncio.sleep(0.03)
            await release_standby("files_api", "k1")

        _ = asyncio.create_task(_releaser())

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=lambda: None,  # 계속 MISS
        )
        assert result is None

    async def test_timeout_returns_none(self, fake_redis, fast_backoff):
        """timeout 도달 시 None + warning (release 안 되고 계속 MISS)."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        # 짧은 timeout — backoff 몇 번 후 만료
        result = await wait_for_standby_release(
            "files_api", "k1", timeout=1,  # 1초 timeout
            find_cache_fn=lambda: None,
        )
        assert result is None

    async def test_no_find_cache_fn_waits_for_standby(self, fake_redis, fast_backoff):
        """find_cache_fn 없이 standby 해제만 감지."""
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)

        async def _releaser():
            await asyncio.sleep(0.03)
            await release_standby("files_api", "k1")

        _ = asyncio.create_task(_releaser())

        result = await wait_for_standby_release(
            "files_api", "k1", timeout=5,
            find_cache_fn=None,
        )
        assert result is None

    async def test_redis_unavailable_raises(self, no_redis):
        with pytest.raises(RuntimeError, match="Redis 미연결"):
            await wait_for_standby_release("files_api", "k1", timeout=1)


# ── release_standby ───────────────────────────────────────────


class TestReleaseStandby:
    async def test_release_removes_key(self, fake_redis):
        await try_acquire_standby("files_api", "k1", ttl_seconds=60)
        assert fake_redis.get("file_cache:files_api_standby:k1") is not None

        await release_standby("files_api", "k1")
        assert fake_redis.get("file_cache:files_api_standby:k1") is None

    async def test_release_nonexistent_is_safe(self, fake_redis):
        """없는 키 release도 예외 없이 통과."""
        await release_standby("files_api", "nonexistent")  # no-op

    async def test_redis_unavailable_does_not_raise(self, no_redis):
        """Redis 미연결 시 조용히 return (finally 블록 보호)."""
        await release_standby("files_api", "k1")  # no raise

    async def test_redis_exception_swallowed(self, monkeypatch):
        """Redis 장애 시 예외 삼킴 (원본 예외 마스킹 방지)."""
        class ExplodingClient:
            def delete(self, *args, **kwargs):
                raise ConnectionError("redis down")

        monkeypatch.setattr(dedup, "get_client", lambda: ExplodingClient())
        await release_standby("files_api", "k1")  # no raise
